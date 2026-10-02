const store = require('./study-store');

function openPage(path, params) {
  const query = Object.keys(params || {}).filter((key) => params[key] !== undefined && params[key] !== null).map((key) => `${encodeURIComponent(key)}=${encodeURIComponent(params[key])}`).join('&');
  const url = path + (query ? `?${query}` : '');
  const pages = typeof getCurrentPages === 'function' ? getCurrentPages() : [];
  if (pages.length >= 9) wx.redirectTo({ url });
  else wx.navigateTo({ url });
}

function continueLearning() {
  const resume = store.getResume();
  if (resume) openPage(resume.path, { ...resume.options, resume: 1 });
  else openPage('/pages/literature/index', { categoryId: 'pre-qin' });
}

module.exports = { openPage, continueLearning };
