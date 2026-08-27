# 配置管理增强 + 审计日志 Network Error 修复计划

## 概述

本次迭代实现两个目标：
1. **配置管理增强**：支持新增配置文件并保存到服务器，按分类（服务器配置、数据库配置、LLM 配置、应用配置）管理
2. **审计日志 Network Error 修复**：解决前端访问审计日志接口报错 Network Error 的问题

---

## 当前状态分析

### 审计日志 Network Error 根因

- 前端 `request.ts` 中 `baseURL: '/api'`，`AuditView.vue` 调用 `request.get('/audit')` → 实际请求 `/api/audit`
- 后端 `audit.py` 路由定义为 `@router.get("/")`，挂载在 `prefix="/api/audit"` → 完整路径为 `/api/audit/`（带尾部斜杠）
- FastAPI 默认 `redirect_slashes=True`，当请求 `/api/audit`（无斜杠）时返回 307 重定向到 `/api/audit/`
- 在 Vite 代理或 Nginx 反向代理环境下，307 重定向可能导致请求无法正确转发，表现为 "Network Error"
- `servers.py` 存在相同的模式（`@router.get("/")` + `prefix="/api/servers"`），同样潜在此问题

### 配置管理现状

- `configs.py` 中使用静态 `PREDEFINED_CONFIGS` 列表，无分类字段
- `ConfigFileInfo` schema 仅有 `path`、`name`、`format` 三个字段，无 `category`
- 不支持新增自定义配置文件
- 前端 `ConfigEditView.vue` 以扁平列表展示配置文件，无分类分组
- 无数据库模型存储用户自定义的配置文件定义

---

## 实施方案

### 第一部分：修复审计日志 Network Error

#### 1.1 后端路由修复 — `src/web/api/audit.py`

**问题**：`@router.get("/")` 匹配 `/api/audit/`，前端请求 `/api/audit` 触发 307 重定向。

**修复**：将 `@router.get("/")` 改为 `@router.get("")`，使路由直接匹配 `/api/audit`（无尾部斜杠），消除重定向。

```python
# 修改前
@router.get("/", response_model=AuditLogPage, summary="查询审计日志")

# 修改后
@router.get("", response_model=AuditLogPage, summary="查询审计日志")
```

#### 1.2 后端路由修复 — `src/web/api/servers.py`

**同样问题**：`@router.get("/")` 匹配 `/api/servers/`，存在相同的 307 重定向隐患。

**修复**：将 `@router.get("/")` 改为 `@router.get("")`。

```python
# 修改前
@router.get("/", response_model=List[ServerInfo], summary="获取服务器列表")

# 修改后
@router.get("", response_model=List[ServerInfo], summary="获取服务器列表")
```

#### 1.3 前端请求路径统一 — `web/src/views/AuditView.vue`

为确保前后端路径完全一致，将前端请求路径也加上尾部斜杠作为双保险：

```typescript
// 修改前（第 87 行）
const res = await request.get('/audit', { params })

// 修改后
const res = await request.get('/audit/', { params })
```

同时检查 `ConfigEditView.vue` 中对 `/servers` 的调用，统一加尾部斜杠：

```typescript
// 修改前
const res = await request.get<ServerItem[] | { items?: ServerItem[] }>('/servers')

// 修改后
const res = await request.get<ServerItem[] | { items?: ServerItem[] }>('/servers/')
```

---

### 第二部分：配置管理增强 — 配置分类与新增

#### 2.1 Schema 扩展 — `src/web/schemas/config_file.py`

在 `ConfigFileInfo` 中增加 `category` 字段，新增 `ConfigCreateRequest` 用于创建配置文件请求。

```python
class ConfigFileInfo(BaseModel):
    path: str = Field(..., description="文件路径")
    name: str = Field(..., description="文件名称")
    format: str = Field(..., description="文件格式: yml/json/conf/properties")
    category: str = Field("other", description="配置分类: server/database/llm/application/other")
    is_custom: bool = Field(False, description="是否为用户自定义配置")

class ConfigCreateRequest(BaseModel):
    """新增配置文件请求"""
    name: str = Field(..., description="配置文件名称")
    path: str = Field(..., description="服务器上的文件路径")
    category: str = Field("application", description="配置分类: server/database/llm/application")
    content: str = Field("", description="初始内容（可为空）")
    description: str = Field("", description="配置描述")
```

#### 2.2 数据库模型 — 新增 `src/web/models/custom_config.py`

创建 `CustomConfig` 模型，存储用户自定义的配置文件定义（非文件内容本身，而是文件路径、分类等元数据）。

```python
class CustomConfig(Base):
    """自定义配置文件定义表"""
    __tablename__ = "custom_configs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="application")
    description: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
```

#### 2.3 模型注册 — `src/web/models/__init__.py`

在 `__init__.py` 和 `database.py` 的 `init_database()` 中注册 `CustomConfig` 模型。

#### 2.4 后端 API 改造 — `src/web/api/configs.py`

**2.4.1 为预定义配置添加分类**

将 `PREDEFINED_CONFIGS` 改为带分类的结构：

```python
PREDEFINED_CONFIGS: List[ConfigFileInfo] = [
    # 服务器配置
    ConfigFileInfo(path="/etc/nginx/nginx.conf", name="nginx.conf", format="conf", category="server"),
    ConfigFileInfo(path="/etc/sysctl.conf", name="sysctl.conf", format="conf", category="server"),
    ConfigFileInfo(path="/etc/ssh/sshd_config", name="sshd_config", format="conf", category="server"),
    ConfigFileInfo(path="/etc/fstab", name="fstab", format="conf", category="server"),
    ConfigFileInfo(path="/etc/hosts", name="hosts", format="conf", category="server"),
    ConfigFileInfo(path="/etc/environment", name="environment", format="conf", category="server"),
    ConfigFileInfo(path="/etc/crontab", name="crontab", format="conf", category="server"),
    ConfigFileInfo(path="/etc/rsyslog.conf", name="rsyslog.conf", format="conf", category="server"),
    # 数据库配置
    ConfigFileInfo(path="/etc/my.cnf", name="my.cnf", format="conf", category="database"),
    ConfigFileInfo(path="/etc/redis/redis.conf", name="redis.conf", format="conf", category="database"),
    ConfigFileInfo(path="/etc/postgresql/postgresql.conf", name="postgresql.conf", format="conf", category="database"),
    # 应用配置
    ConfigFileInfo(path="/opt/app/application.yml", name="application.yml", format="yml", category="application"),
    ConfigFileInfo(path="/opt/app/application.yaml", name="application.yaml", format="yml", category="application"),
    ConfigFileInfo(path="/opt/app/application.properties", name="application.properties", format="properties", category="application"),
]
```

**2.4.2 修改 `list_config_files` 端点**

合并预定义配置和数据库中的自定义配置，返回带分类信息的完整列表：

```python
@router.get("/{host}/list", response_model=List[ConfigFileInfo], summary="获取配置文件列表")
def list_config_files(
    host: str,
    category: Optional[str] = Query(None, description="按分类筛选"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    configs = list(PREDEFINED_CONFIGS)
    # 从数据库加载自定义配置
    custom_configs = db.query(CustomConfig).all()
    for cc in custom_configs:
        configs.append(ConfigFileInfo(
            path=cc.file_path,
            name=cc.name,
            format=_detect_format(cc.file_path),
            category=cc.category,
            is_custom=True,
        ))
    if category:
        configs = [c for c in configs if c.category == category]
    return configs
```

**2.4.3 新增 `POST /{host}/create` 端点**

创建新配置文件并保存到服务器，同时在数据库中记录自定义配置定义：

```python
@router.post("/{host}/create", response_model=ConfigFileInfo, summary="新增配置文件")
async def create_config_file(
    host: str,
    request: ConfigCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
):
    # 1. 检查路径是否已存在（预定义或自定义）
    # 2. 通过 SSH 在服务器上创建文件（写入初始内容）
    # 3. 在数据库中保存自定义配置记录
    # 4. 返回 ConfigFileInfo
```

**2.4.4 新增 `DELETE /{host}/custom/{config_id}` 端点**

删除自定义配置定义（仅删除数据库记录，不删除服务器上的文件）：

```python
@router.delete("/{host}/custom/{config_id}", summary="删除自定义配置")
def delete_custom_config(
    host: str,
    config_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operator),
):
    # 删除数据库中的自定义配置记录
```

**2.4.5 新增 `GET /categories` 端点**

返回所有配置分类及其数量，供前端展示分类标签：

```python
@router.get("/categories", summary="获取配置分类列表")
def get_categories(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    # 返回 [{"category": "server", "label": "服务器配置", "count": 8}, ...]
```

#### 2.5 前端改造 — `web/src/views/ConfigEditView.vue`

**2.5.1 接口扩展**

```typescript
interface ConfigFile {
  path: string
  name?: string
  format?: string
  category?: string
  is_custom?: boolean
  [key: string]: unknown
}

interface CategoryInfo {
  category: string
  label: string
  count: number
}
```

**2.5.2 分类标签映射**

```typescript
const categoryLabels: Record<string, string> = {
  server: '服务器配置',
  database: '数据库配置',
  llm: 'LLM 配置',
  application: '应用配置',
  other: '其他',
}
```

**2.5.3 左侧面板改造**

将扁平的 `el-menu` 改为 `el-collapse` 折叠面板，每个分类一个折叠项：

```vue
<el-collapse v-model="activeCategories">
  <el-collapse-item
    v-for="cat in categories"
    :key="cat.category"
    :title="cat.label"
    :name="cat.category"
  >
    <el-menu ...>
      <el-menu-item v-for="f in filesByCategory[cat.category]" ...>
        ...
      </el-menu-item>
    </el-menu>
  </el-collapse-item>
</el-collapse>
```

**2.5.4 新增配置文件对话框**

在左侧面板顶部添加「新增配置」按钮，点击后弹出对话框：

```vue
<el-dialog title="新增配置文件" v-model="createDialogVisible">
  <el-form>
    <el-form-item label="分类">
      <el-select v-model="createForm.category">
        <el-option label="服务器配置" value="server" />
        <el-option label="数据库配置" value="database" />
        <el-option label="LLM 配置" value="llm" />
        <el-option label="应用配置" value="application" />
      </el-select>
    </el-form-item>
    <el-form-item label="文件名称">
      <el-input v-model="createForm.name" />
    </el-form-item>
    <el-form-item label="文件路径">
      <el-input v-model="createForm.path" placeholder="/etc/myapp/config.yml" />
    </el-form-item>
    <el-form-item label="初始内容">
      <el-input type="textarea" v-model="createForm.content" :rows="6" />
    </el-form-item>
    <el-form-item label="描述">
      <el-input v-model="createForm.description" />
    </el-form-item>
  </el-form>
  <template #footer>
    <el-button @click="createDialogVisible = false">取消</el-button>
    <el-button type="primary" @click="confirmCreate">创建并保存</el-button>
  </template>
</el-dialog>
```

**2.5.5 创建配置逻辑**

```typescript
async function confirmCreate() {
  await request.post(
    `/configs/${encodeURIComponent(selectedHost.value)}/create`,
    createForm
  )
  ElMessage.success('配置文件已创建')
  createDialogVisible.value = false
  await loadConfigList()
}
```

**2.5.6 分类筛选与文件分组**

```typescript
const filesByCategory = computed(() => {
  const groups: Record<string, ConfigFile[]> = {}
  for (const f of configFiles.value) {
    const cat = (f.category as string) || 'other'
    if (!groups[cat]) groups[cat] = []
    groups[cat].push(f)
  }
  return groups
})
```

---

## 修改文件清单

| 文件 | 修改内容 |
|------|----------|
| `src/web/api/audit.py` | 路由从 `@router.get("/")` 改为 `@router.get("")` |
| `src/web/api/servers.py` | 路由从 `@router.get("/")` 改为 `@router.get("")` |
| `web/src/views/AuditView.vue` | 请求路径加尾部斜杠 `/audit/` |
| `web/src/views/ConfigEditView.vue` | 请求路径加尾部斜杠 `/servers/` |
| `src/web/schemas/config_file.py` | `ConfigFileInfo` 增加 `category` 和 `is_custom` 字段；新增 `ConfigCreateRequest` |
| `src/web/models/custom_config.py` | 新建 `CustomConfig` 模型 |
| `src/web/models/__init__.py` | 导出 `CustomConfig` |
| `src/web/database.py` | `init_database()` 中导入 `CustomConfig` |
| `src/web/api/configs.py` | 预定义配置加分类；修改 `list_config_files` 合并自定义配置；新增 `create`、`delete`、`categories` 端点 |
| `web/src/views/ConfigEditView.vue` | 左侧面板改为分类折叠展示；新增配置文件对话框和创建逻辑 |

---

## 假设与决策

1. **分类方案**：使用固定四类（server/database/llm/application）+ other 兜底，不做动态分类管理，降低复杂度
2. **LLM 配置分类**：预定义配置中暂无 LLM 相关配置文件路径（LLM 配置通常在 ops-agent 自身的 config.yaml 中），此分类预留给用户自定义添加
3. **自定义配置存储**：数据库只存储配置文件的元数据（路径、名称、分类），文件内容仍存储在远程服务器上
4. **创建配置文件**：通过 SSH 在目标服务器上创建文件并写入初始内容，同时在数据库中记录元数据
5. **删除自定义配置**：仅删除数据库记录，不删除服务器上的实际文件（避免误删重要配置）
6. **审计日志修复方案**：优先修改后端路由定义消除 307 重定向，同时前端加尾部斜杠作为双保险

---

## 验证步骤

1. **审计日志验证**：
   - 启动后端 `python -m src.main --mode web`
   - 启动前端 `npm run dev`
   - 登录后访问审计日志页面，确认日志列表正常加载，无 Network Error
   - 检查浏览器开发者工具 Network 面板，确认 `/api/audit` 请求返回 200 而非 307

2. **配置管理验证**：
   - 访问配置管理页面，确认左侧面板按分类（服务器配置、数据库配置、LLM 配置、应用配置）折叠展示
   - 点击「新增配置」，填写分类/名称/路径/初始内容，确认文件在服务器上创建成功
   - 刷新配置列表，确认新增的自定义配置出现在对应分类下
   - 选择自定义配置文件，编辑内容并保存，确认保存成功且历史记录正常
   - 按分类筛选配置文件，确认筛选功能正常

3. **服务器列表验证**：
   - 访问配置管理页面，确认服务器下拉列表正常加载（验证 servers 路由修复未引入回归）
