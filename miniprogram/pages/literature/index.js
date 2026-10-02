const dataService = require('../../services/data-service');
const studyStore = require('../../utils/study-store');

const PAGE_SIZE = 5;
const PAGE_PATH = '/pages/literature/index';

Page({
  data: {
    categories: [],
    categoryId: '',
    categoryName: '先秦文学',
    title: '文学常识 · 先秦文学',
    mode: 'writing',
    questions: [],
    groupIndex: 0,
    pageSize: PAGE_SIZE,
    groupTotal: 0,
    totalQuestions: 0,
    isLastGroup: true,
    categoryScrollLeft: 0
  },

  onLoad(options) {
    const categories = dataService.getCategories('literature');
    const stored = options.resume === '1' ? studyStore.getResume() : null;
    const resume = stored && stored.path === PAGE_PATH ? stored : null;
    const resumeOptions = resume && resume.options ? resume.options : {};
    const requestedCategory = options.categoryId || resumeOptions.categoryId;
    const category = categories.find((item) => item.id === requestedCategory) || categories[0];
    const requestedMode = options.mode || resumeOptions.mode || studyStore.getMode();
    const mode = requestedMode === 'reciting' ? 'reciting' : 'writing';
    const requestedIndex = options.groupIndex !== undefined
      ? options.groupIndex
      : (resume ? resume.groupIndex : 0);
    this._resumeIds = resume && Array.isArray(resume.groupIds) ? resume.groupIds : null;
    studyStore.setMode(mode);
    this.setData({
      categories,
      categoryId: category ? category.id : '',
      categoryName: category ? category.name : '文学常识',
      title: category ? `文学常识 · ${category.name}` : '文学常识',
      mode,
      groupIndex: Math.max(0, Number(requestedIndex) || 0),
      categoryScrollLeft: Math.max(0, categories.indexOf(category) - 1) * 78
    });
    this.refreshQuestions(true);
  },

  onShow() {
    this.setData({ mode: studyStore.getMode() });
    this.refreshQuestions(false);
  },

  refreshQuestions(restore) {
    if (!this.data.categoryId) {
      this.setData({ questions: [], groupTotal: 0, totalQuestions: 0 });
      return;
    }
    const items = dataService.getQuestions({
      source: 'literature',
      categoryId: this.data.categoryId
    });
    this._allQuestions = items;
    const groupTotal = Math.ceil(items.length / PAGE_SIZE);
    let groupIndex = Math.min(this.data.groupIndex, Math.max(0, groupTotal - 1));
    if (restore && this._resumeIds && this._resumeIds.length) {
      const firstIndex = items.findIndex((question) => question.id === this._resumeIds[0]);
      if (firstIndex >= 0) groupIndex = Math.floor(firstIndex / PAGE_SIZE);
    }
    const currentGroup = items.slice(groupIndex * PAGE_SIZE, (groupIndex + 1) * PAGE_SIZE);
    this.setData({
      groupIndex,
      groupTotal,
      totalQuestions: items.length,
      questions: dataService.decorateQuestions(currentGroup),
      isLastGroup: groupIndex >= groupTotal - 1
    });
    this.saveProgress();
  },

  saveProgress() {
    if (!this.data.questions.length) return;
    studyStore.saveResume({
      path: PAGE_PATH,
      options: { categoryId: this.data.categoryId, mode: this.data.mode },
      groupIds: this.data.questions.map((question) => question.id),
      groupIndex: this.data.groupIndex
    });
  },

  changeCategory(event) {
    const categoryId = event.currentTarget.dataset.id;
    if (categoryId === this.data.categoryId) return;
    const categoryIndex = this.data.categories.findIndex((item) => item.id === categoryId);
    const category = this.data.categories[categoryIndex];
    if (!category) return;
    this.setData({
      categoryId,
      categoryName: category.name,
      title: `文学常识 · ${category.name}`,
      groupIndex: 0,
      categoryScrollLeft: Math.max(0, categoryIndex - 1) * 78
    });
    this._resumeIds = null;
    this.refreshQuestions(false);
  },

  changeMode(event) {
    const mode = event.detail.mode;
    if (mode !== 'writing' && mode !== 'reciting') return;
    studyStore.setMode(mode);
    this.setData({ mode });
    this.saveProgress();
  },

  toggleFavorite(event) {
    if (!event.detail.id) return;
    studyStore.toggleFavorite(event.detail.id);
    this.refreshQuestions(false);
  },

  toggleMastered(event) {
    if (!event.detail.id) return;
    studyStore.toggleMastered(event.detail.id);
    this.refreshQuestions(false);
  },

  nextGroup() {
    const nextIndex = this.data.isLastGroup ? 0 : this.data.groupIndex + 1;
    this.setData({ groupIndex: nextIndex });
    this.refreshQuestions(false);
    wx.pageScrollTo({ scrollTop: 0, duration: 180 });
  },

  showFirstCategory() {
    const category = this.data.categories[0];
    if (!category || category.id === this.data.categoryId) return;
    this.setData({
      categoryId: category.id,
      categoryName: category.name,
      title: `文学常识 · ${category.name}`,
      groupIndex: 0,
      categoryScrollLeft: 0
    });
    this.refreshQuestions(false);
  }
});
