const mock = require('../data/mock');
const STORAGE_KEY = 'chinese-study.frontend.v1';
const validIds = new Set(mock.questions.map((question) => question.id));
let cached;

function todayKey(now) {
  const date = new Date(now === undefined ? Date.now() : now);
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${date.getFullYear()}-${month}-${day}`;
}

function freshState() {
  return { version: 1, favorites: [], mastered: {}, studyDates: [], mode: 'writing', resume: null };
}

function normalize(input) {
  if (!input || typeof input !== 'object' || input.version !== 1) return freshState();
  const mastered = {};
  Object.keys(input.mastered || {}).forEach((id) => {
    if (validIds.has(id) && /^\d{4}-\d{2}-\d{2}$/.test(input.mastered[id])) mastered[id] = input.mastered[id];
  });
  const favorites = Array.isArray(input.favorites) ? input.favorites.filter((id) => validIds.has(id)) : [];
  const studyDates = Array.isArray(input.studyDates) ? input.studyDates.filter((day) => /^\d{4}-\d{2}-\d{2}$/.test(day)) : [];
  const allowedPaths = ['/pages/literature/index', '/pages/practice/index', '/pages/random/index'];
  const resume = input.resume && allowedPaths.indexOf(input.resume.path) !== -1 ? input.resume : null;
  return { version: 1, favorites: Array.from(new Set(favorites)), mastered, studyDates: Array.from(new Set(studyDates)), mode: input.mode === 'reciting' ? 'reciting' : 'writing', resume };
}

function getState() {
  if (!cached) {
    try { cached = normalize(typeof wx !== 'undefined' ? wx.getStorageSync(STORAGE_KEY) : null); }
    catch (_) { cached = freshState(); }
  }
  return JSON.parse(JSON.stringify(cached));
}

function save(state) {
  cached = normalize(state);
  try {
    if (typeof wx !== 'undefined') wx.setStorageSync(STORAGE_KEY, cached);
  } catch (_) {
    if (typeof wx !== 'undefined' && wx.showToast) wx.showToast({ title: '记录暂未保存，请稍后重试', icon: 'none' });
  }
  return getState();
}

function toggleFavorite(id) {
  if (!validIds.has(id)) return false;
  const state = getState();
  const index = state.favorites.indexOf(id);
  if (index === -1) state.favorites.push(id);
  else state.favorites.splice(index, 1);
  save(state);
  return index === -1;
}

function toggleMastered(id) {
  if (!validIds.has(id)) return false;
  const state = getState();
  const already = Object.prototype.hasOwnProperty.call(state.mastered, id);
  if (already) delete state.mastered[id];
  else {
    const date = todayKey();
    state.mastered[id] = date;
    if (state.studyDates.indexOf(date) === -1) state.studyDates.push(date);
  }
  save(state);
  return !already;
}

function getMode() { return getState().mode; }
function setMode(mode) {
  const state = getState();
  state.mode = mode === 'reciting' ? 'reciting' : 'writing';
  save(state);
  return state.mode;
}

function saveResume(record) {
  const state = getState();
  state.resume = record ? {
    path: record.path,
    options: record.options || {},
    groupIds: Array.isArray(record.groupIds) ? record.groupIds.filter((id) => validIds.has(id)) : [],
    groupIndex: Math.max(0, Math.floor(Number(record.groupIndex) || 0))
  } : null;
  save(state);
}
function getResume() { return getState().resume; }

function getStats(now) {
  const state = getState();
  const ids = Object.keys(state.mastered);
  const articleLearnedCount = mock.articles.filter((article) => {
    const items = mock.questions.filter((question) => question.articleId === article.id);
    return items.length > 0 && items.every((question) => state.mastered[question.id]);
  }).length;
  const date = new Date(now === undefined ? Date.now() : now);
  const dayStart = new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  const examParts = mock.config.examDate.split('-').map(Number);
  const exam = new Date(examParts[0], examParts[1] - 1, examParts[2]).getTime();
  return {
    masteredCount: ids.length, studyDays: state.studyDates.length,
    completion: mock.questions.length ? Math.round(ids.length * 100 / mock.questions.length) : 0,
    todayCount: ids.filter((id) => state.mastered[id] === todayKey(now)).length,
    todayTarget: mock.config.dailyTarget,
    daysUntilExam: Math.max(0, Math.ceil((exam - dayStart) / 86400000)),
    totalQuestions: mock.questions.length, articleLearnedCount,
    articleTotal: mock.articles.length, favoriteCount: state.favorites.length
  };
}

module.exports = { STORAGE_KEY, getState, getStats, getMode, setMode, toggleFavorite, toggleMastered, saveResume, getResume, todayKey };
