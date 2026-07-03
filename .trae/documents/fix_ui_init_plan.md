# 修复UI初始化缺失问题

## 摘要

修复UI优化中丢失的JavaScript初始化代码，恢复榜单页面显示和按钮点击功能。

## 问题分析

### 根因
旧版本 `index_new.html` 中有完整的初始化流程：
- `async function init()` 函数
- `init();` 调用

但在本次UI优化中，这些初始化代码被意外删除，导致：
- `parseCategories()` 未调用 → 手动转存页面的榜单分类不显示
- `parseSchedCats()` 未调用 → 定时任务页面的榜单分类不显示
- `loadSchedule()` 未调用 → 定时任务配置无法加载
- `loadExecHistory()` 未调用 → 执行历史无法加载
- 初始化过程中也没有加载categories数据

### 数据流分析

```
页面加载 → init() → loadCategories() → parseCategories() / parseSchedCats()
                                    → loadSchedule()
                                    → loadExecHistory()
                                    → loadDashboard()
```

## 修复方案

### 1. 添加初始化函数

**文件**: `static/index_new.html`

**修改内容**:
- 添加 `async function init()` 函数
- 在函数末尾添加 `init();` 调用
- 确保按正确顺序调用：加载categories → 解析分类 → 加载配置 → 加载历史

### 2. 确保categories数据加载

**文件**: `static/index_new.html`

**修改内容**:
- 在初始化过程中调用 `/api/categories` 获取分类数据
- 将数据赋值给全局变量 `C`

## 实施步骤

### 第1步：添加初始化函数

在JavaScript代码末尾添加：

```javascript
async function init(){
  try{
    var d = await apiGet('/api/categories');
    C = d;
    parseCategories();
    parseSchedCats();
    loadSchedule();
    loadExecHistory();
    initTheme();
  }catch(e){
    console.error('初始化失败:', e);
    showToast('初始化失败', false);
  }
}
init();
```

### 第2步：测试验证

- 运行项目测试
- 验证页面功能正常

## 文件修改清单

| 文件 | 修改类型 | 说明 |
|------|---------|------|
| `static/index_new.html` | 修改 | 添加初始化函数和调用 |

## 验证标准

1. ✅ 手动转存页面的榜单分类正常显示
2. ✅ 定时任务页面的榜单分类正常显示
3. ✅ 按钮点击功能正常
4. ✅ 执行历史正常加载
5. ✅ 测试通过