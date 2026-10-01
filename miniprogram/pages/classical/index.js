const dataService = require('../../services/data-service');
const studyStore = require('../../utils/study-store');
const navigation = require('../../utils/navigation');

Page({
  data: {
    filterStatus: 'all',
    filters: [
      { value: 'all', label: '全部' },
      { value: 'unlearned', label: '未学' },
      { value: 'learning', label: '练习中' },
      { value: 'learned', label: '已学' }
    ],
    visibleArticles: [],
    visibleCount: 0,
    articleTotal: 25,
    learnedCount: 0,
    completionPercent: 0,
    modalVisible: false,
    selectedArticle: null
  },

  onShow() {
    this.refreshProgress();
  },

  onReady() {
    this.canvasReady = true;
    this.drawProgress();
  },

  refreshProgress() {
    this.articles = dataService.getArticleProgress();
    const stats = studyStore.getStats();
    const articleTotal = stats.articleTotal || this.articles.length;
    const learnedCount = Number(stats.articleLearnedCount) || 0;
    this.setData({
      articleTotal,
      learnedCount,
      completionPercent: articleTotal ? Math.round(learnedCount / articleTotal * 100) : 0
    });
    this.applyFilter();
    if (this.canvasReady) wx.nextTick(() => this.drawProgress());
  },

  applyFilter() {
    const status = this.data.filterStatus;
    const articles = (this.articles || []).filter((article) => status === 'all' || article.status === status);
    this.setData({
      visibleArticles: articles,
      visibleCount: articles.length
    });
  },

  drawProgress() {
    this.createSelectorQuery().select('#article-progress').fields({ node: true, size: true }).exec((results) => {
      const result = results && results[0];
      if (!result || !result.node || !result.width) return;
      const canvas = result.node;
      const context = canvas.getContext('2d');
      const windowInfo = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
      const ratio = windowInfo.pixelRatio || 1;
      canvas.width = result.width * ratio;
      canvas.height = result.height * ratio;
      context.scale(ratio, ratio);
      const centerX = result.width / 2;
      const centerY = result.height / 2;
      const lineWidth = result.width * 0.077;
      const radius = Math.min(centerX, centerY) - lineWidth;
      context.lineWidth = lineWidth;
      context.lineCap = 'round';
      context.strokeStyle = '#E6EBE5';
      context.beginPath();
      context.arc(centerX, centerY, radius, 0, Math.PI * 2);
      context.stroke();
      if (this.data.completionPercent > 0) {
        context.strokeStyle = '#1B4D3E';
        context.beginPath();
        context.arc(centerX, centerY, radius, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * this.data.completionPercent / 100);
        context.stroke();
      }
    });
  },

  onFilter(event) {
    this.setData({ filterStatus: event.currentTarget.dataset.status });
    this.applyFilter();
  },

  onOpenArticle(event) {
    const article = (this.articles || []).find((item) => item.id === event.currentTarget.dataset.id);
    if (article) this.setData({ selectedArticle: article, modalVisible: true });
  },

  onCloseModal() {
    this.setData({ modalVisible: false });
  },

  stopTap() {},

  onChooseType(event) {
    const article = this.data.selectedArticle;
    if (!article) return;
    this.setData({ modalVisible: false });
    navigation.openPage('/pages/practice/index', { articleId: article.id, type: event.currentTarget.dataset.type });
  },

  onSpecialty(event) {
    navigation.openPage('/pages/practice/index', { type: event.currentTarget.dataset.type });
  },

  onContinue() {
    const resume = studyStore.getResume();
    if (resume && /pages\/practice\/index$/.test(resume.path || '')) {
      navigation.continueLearning();
      return;
    }
    const questions = dataService.decorateQuestions(dataService.getQuestions({ source: 'classical' }))
      .filter((question) => question.type === 'word' || question.type === 'translation');
    const articles = (this.articles || []).filter((article) => questions.some((question) => question.articleId === article.id));
    const article = articles.find((item) => questions.some((question) => question.articleId === item.id && !question.mastered)) || articles[0];
    if (!article) {
      wx.showToast({ title: '暂无可学习的演示内容', icon: 'none' });
      return;
    }
    const articleQuestions = questions.filter((question) => question.articleId === article.id);
    const nextQuestion = articleQuestions.find((question) => !question.mastered) || articleQuestions[0];
    navigation.openPage('/pages/practice/index', { articleId: article.id, type: nextQuestion.type });
  }
});
