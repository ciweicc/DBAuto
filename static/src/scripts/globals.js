// ============ Global ============
var C = {}, currentTab = 'manual', execHistoryData = [], execHistoryFilter = 'all';
var expiredDirEntries = [], autoSaveTimer = null, logPollTimer = null;
var logBefore = [], logFilter = 'all', logPaused = false;
var logPollInterval = 0;
var sseConnected = false;
var LOG_BEFORE_MAX = 500;
var APP_PATHS = {category_base:'/影视', search:'/批量转存/手动搜索存', tmdb:'/批量转存/TMDB'};
var SETTINGS_ALL = null;  // 缓存 /api/settings/all，调度页与设置页共用，避免重复请求

