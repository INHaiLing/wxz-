const dataService = require('../../services/data-service');
const studyStore = require('../../utils/study-store');
const { chunk } = require('../../utils/question-utils');
const navigation = require('../../utils/navigation');

Page({
  data: {
    categoryRows: [],
    categoryId: '',
    activeCategoryElement: '',
    activeCategoryRow: 'literature',
    mode: 'writing',
    questions: [],
    pageNumber: 0,
    pageCount: 0,
    totalFavorites: 0,
    nextLabel: '下一页'
  },

  onLoad(options = {}) {
    this._categories = dataService.getCategories('favorites');
    this._pageIndex = 0;
    this._categoryId = options.categoryId || '';
    const favorites = new Set(studyStore.getState().favorites);
    if (!this._categories.some((category) => category.id === this._categoryId)) {
      const firstPopulated = this._categories.find((category) =>
        dataService.getQuestions({ categoryId: category.id }).some((question) => favorites.has(question.id))
      );
      this._categoryId = firstPopulated
        ? firstPopulated.id
        : (this._categories[0] && this._categories[0].id) || '';
    }
    const literary = this._categories.filter((category) => category.id.indexOf('classical-') !== 0);
    const classical = this._categories.filter((category) => category.id.indexOf('classical-') === 0);
    this.setData({
      categoryRows: [
        { id: 'literature', items: literary },
        { id: 'classical', items: classical }
      ],
      activeCategoryElement: `category-${this._categoryId}`,
      activeCategoryRow: this._categoryId.indexOf('classical-') === 0 ? 'classical' : 'literature',
      mode: studyStore.getMode()
    });
    this._ready = true;
    this._refreshFavorites();
  },

  onShow() {
    if (this._ready) this._refreshFavorites();
  },

  _refreshFavorites() {
    const favoriteIds = new Set(studyStore.getState().favorites);
    const items = dataService.getQuestions({ categoryId: this._categoryId })
      .filter((question) => favoriteIds.has(question.id));
    const groups = chunk(items, 5);
    this._pageIndex = Math.max(0, Math.min(this._pageIndex, Math.max(groups.length - 1, 0)));
    const questions = groups[this._pageIndex] || [];
    this.setData({
      categoryId: this._categoryId,
      mode: studyStore.getMode(),
      questions: dataService.decorateQuestions(questions),
      pageNumber: groups.length ? this._pageIndex + 1 : 0,
      pageCount: groups.length,
      totalFavorites: favoriteIds.size,
      nextLabel: this._pageIndex >= groups.length - 1 && groups.length > 1
        ? '回到第一页'
        : '下一页'
    });
  },

  onCategoryChange(event) {
    const categoryId = event.currentTarget.dataset.id;
    if (categoryId === this._categoryId) return;
    this._categoryId = categoryId;
    this._pageIndex = 0;
    this.setData({
      activeCategoryElement: `category-${categoryId}`,
      activeCategoryRow: categoryId.indexOf('classical-') === 0 ? 'classical' : 'literature'
    });
    this._refreshFavorites();
  },

  onModeChange(event) {
    const mode = event.detail.mode;
    if (mode !== 'writing' && mode !== 'reciting') return;
    studyStore.setMode(mode);
    this._refreshFavorites();
  },

  onFavorite(event) {
    studyStore.toggleFavorite(event.detail.id);
    this._refreshFavorites();
  },

  onMaster(event) {
    studyStore.toggleMastered(event.detail.id);
    this._refreshFavorites();
  },

  onNextPage() {
    if (this.data.pageCount <= 1) return;
    this._pageIndex = (this._pageIndex + 1) % this.data.pageCount;
    this._refreshFavorites();
    if (typeof wx !== 'undefined' && wx.pageScrollTo) wx.pageScrollTo({ scrollTop: 0, duration: 200 });
  },

  onGoStudy() {
    if (this._categoryId.indexOf('classical-') === 0) {
      navigation.openPage('/pages/classical/index');
    } else {
      navigation.openPage('/pages/literature/index', { categoryId: this._categoryId });
    }
  }
});
