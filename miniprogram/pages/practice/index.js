const dataService = require('../../services/data-service');
const studyStore = require('../../utils/study-store');
const navigation = require('../../utils/navigation');

const GROUP_SIZE = 5;

Page({
  data: {
    title: '重点字词专项',
    typeLabel: '重点字词',
    isWord: true,
    mode: 'writing',
    questions: [],
    total: 0,
    completed: 0,
    progressPercent: 0,
    groupIndex: 0,
    groupCount: 0,
    nextLabel: '下一组'
  },

  onLoad(options) {
    this.articleId = options.articleId || '';
    this.questionType = options.type === 'translation' ? 'translation' : 'word';
    this.allQuestions = dataService.getQuestions({ source: 'classical', articleId: this.articleId || undefined, type: this.questionType });
    const article = dataService.getArticles().find((item) => item.id === this.articleId);
    let groupIndex = 0;
    if (options.resume === '1') {
      const resume = studyStore.getResume();
      const savedOptions = resume && resume.options || {};
      if (resume && /pages\/practice\/index$/.test(resume.path || '') && (savedOptions.articleId || '') === this.articleId && savedOptions.type === this.questionType) {
        const savedIds = Array.isArray(resume.groupIds) ? Array.from(new Set(resume.groupIds)) : [];
        const questionsById = new Map(this.allQuestions.map((question) => [question.id, question]));
        const orderedQuestions = savedIds.map((id) => questionsById.get(id)).filter(Boolean);
        const restoredIds = new Set(orderedQuestions.map((question) => question.id));
        this.allQuestions = orderedQuestions.concat(this.allQuestions.filter((question) => !restoredIds.has(question.id)));
        groupIndex = Number.isInteger(resume.groupIndex) ? resume.groupIndex : 0;
      }
    }
    const groupCount = Math.ceil(this.allQuestions.length / GROUP_SIZE);
    this.setData({
      title: article
        ? article.title + ' · ' + (this.questionType === 'word' ? '重点字词' : '重点翻译')
        : this.questionType === 'word' ? '重点字词专项' : '重点翻译专项',
      typeLabel: this.questionType === 'word' ? '重点字词' : '重点翻译',
      isWord: this.questionType === 'word',
      groupIndex: groupCount ? Math.min(Math.max(groupIndex, 0), groupCount - 1) : 0,
      groupCount
    });
  },

  onShow() {
    this.refreshQuestions();
    if (this.allQuestions && this.allQuestions.length) this.savePosition();
  },

  refreshQuestions() {
    const decorated = dataService.decorateQuestions(this.allQuestions || []);
    const start = this.data.groupIndex * GROUP_SIZE;
    const total = decorated.length;
    const completed = decorated.filter((question) => question.mastered).length;
    const groupCount = Math.ceil(total / GROUP_SIZE);
    this.setData({
      mode: studyStore.getMode(),
      questions: decorated.slice(start, start + GROUP_SIZE),
      total,
      completed,
      progressPercent: total ? Math.round(completed / total * 100) : 0,
      groupCount,
      nextLabel: this.data.groupIndex >= groupCount - 1 ? '再练一轮' : '下一组'
    });
  },

  savePosition() {
    studyStore.saveResume({
      path: '/pages/practice/index',
      options: { articleId: this.articleId, type: this.questionType },
      groupIds: (this.allQuestions || []).map((question) => question.id),
      groupIndex: this.data.groupIndex
    });
  },

  onModeChange(event) {
    const mode = event.detail.mode;
    if (mode !== 'writing' && mode !== 'reciting') return;
    studyStore.setMode(mode);
    this.setData({ mode });
  },

  onFavorite(event) {
    studyStore.toggleFavorite(event.detail.id);
    this.refreshQuestions();
  },

  onMaster(event) {
    studyStore.toggleMastered(event.detail.id);
    this.refreshQuestions();
  },

  onNextGroup() {
    if (!this.data.total) return;
    this.setData({ groupIndex: this.data.groupIndex >= this.data.groupCount - 1 ? 0 : this.data.groupIndex + 1 });
    this.refreshQuestions();
    this.savePosition();
    wx.pageScrollTo({ scrollTop: 0, duration: 180 });
  },

  onEmptyAction() {
    if (getCurrentPages().length > 1) wx.navigateBack({ delta: 1 });
    else navigation.openPage('/pages/classical/index');
  }
});
