const dataService = require('../../services/data-service');
const studyStore = require('../../utils/study-store');
const { shuffle, chunk } = require('../../utils/question-utils');
const navigation = require('../../utils/navigation');

const PAGE_PATH = '/pages/random/index';

Page({
  data: {
    source: 'literature',
    mode: 'writing',
    sourceName: '文学常识',
    subtitle: '分组巩固 · 一轮不重复',
    questions: [],
    groupNumber: 0,
    groupCount: 0,
    roundQuestionCount: 0,
    nextLabel: '下一组'
  },

  onLoad(options = {}) {
    this._source = options.source === 'classical' ? 'classical' : 'literature';
    this._groupIndex = 0;
    this._roundIds = [];
    this._groups = [];
    this.setData({
      source: this._source,
      mode: studyStore.getMode(),
      sourceName: this._source === 'classical' ? '文言文' : '文学常识',
      subtitle: '分组巩固 · 一轮不重复'
    });

    const resume = options.resume === '1' ? studyStore.getResume() : null;
    if (!this._restoreRound(resume)) this._startRound();
    this._ready = true;
    this._refreshGroup();
  },

  onShow() {
    if (this._ready) this._refreshGroup();
  },

  _restoreRound(resume) {
    if (!resume || resume.path !== PAGE_PATH || !Array.isArray(resume.groupIds)) return false;
    if (!resume.options || resume.options.source !== this._source) return false;
    const seen = new Set();
    const ids = resume.groupIds.filter((id) => {
      const question = dataService.getQuestion(id);
      if (!question || question.source !== this._source || seen.has(id)) return false;
      seen.add(id);
      return true;
    });
    if (!ids.length) return false;
    this._roundIds = ids;
    this._groups = chunk(ids, 5);
    this._groupIndex = Math.max(0, Math.min(
      Math.floor(Number(resume.groupIndex) || 0),
      this._groups.length - 1
    ));
    return true;
  },

  _startRound() {
    const seen = new Set();
    const questions = dataService.getQuestions({ source: this._source }).filter((question) => {
      if (!question || !question.id || seen.has(question.id)) return false;
      seen.add(question.id);
      return true;
    });
    this._roundIds = shuffle(questions).map((question) => question.id);
    this._groups = chunk(this._roundIds, 5);
    this._groupIndex = 0;
  },

  _refreshGroup() {
    const ids = this._groups[this._groupIndex] || [];
    const questions = ids.map((id) => dataService.getQuestion(id)).filter(Boolean);
    const count = this._groups.length;
    this.setData({
      mode: studyStore.getMode(),
      questions: dataService.decorateQuestions(questions),
      groupNumber: count ? this._groupIndex + 1 : 0,
      groupCount: count,
      roundQuestionCount: this._roundIds.length,
      nextLabel: this._groupIndex >= count - 1 ? '再练一轮' : '下一组'
    });
    if (count) {
      studyStore.saveResume({
        path: PAGE_PATH,
        options: { source: this._source },
        groupIds: this._roundIds.slice(),
        groupIndex: this._groupIndex
      });
    }
  },

  onModeChange(event) {
    const mode = event.detail.mode;
    if (mode !== 'writing' && mode !== 'reciting') return;
    studyStore.setMode(mode);
    this._refreshGroup();
  },

  onFavorite(event) {
    studyStore.toggleFavorite(event.detail.id);
    this._refreshGroup();
  },

  onMaster(event) {
    studyStore.toggleMastered(event.detail.id);
    this._refreshGroup();
  },

  onNextGroup() {
    if (!this._groups.length) return;
    if (this._groupIndex < this._groups.length - 1) this._groupIndex += 1;
    else this._startRound();
    this._refreshGroup();
    if (typeof wx !== 'undefined' && wx.pageScrollTo) wx.pageScrollTo({ scrollTop: 0, duration: 200 });
  },

  onGoStudy() {
    navigation.openPage(this._source === 'classical'
      ? '/pages/classical/index'
      : '/pages/literature/index');
  }
});
