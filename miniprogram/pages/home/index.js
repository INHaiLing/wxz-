const dataService = require('../../services/data-service');
const studyStore = require('../../utils/study-store');
const navigation = require('../../utils/navigation');

Page({
  data: {
    stats: {
      masteredCount: 0,
      studyDays: 0,
      completion: 0,
      todayCount: 0,
      todayTarget: 20,
      daysUntilExam: 0,
      favoriteCount: 0
    },
    literatureCount: 0,
    literatureMastered: 0,
    classicalCount: 0,
    classicalMastered: 0,
    articleCount: 0,
    todayPercent: 0,
    countdown: 0
  },

  onShow() {
    const stats = studyStore.getStats();
    const literature = dataService.decorateQuestions(
      dataService.getQuestions({ source: 'literature' })
    );
    const classical = dataService.decorateQuestions(
      dataService.getQuestions({ source: 'classical' })
    );
    this.setData({
      stats,
      literatureCount: literature.length,
      literatureMastered: literature.filter((question) => question.mastered).length,
      classicalCount: classical.length,
      classicalMastered: classical.filter((question) => question.mastered).length,
      articleCount: dataService.getArticles().length,
      todayPercent: stats.todayTarget
        ? Math.min(100, Math.round(stats.todayCount / stats.todayTarget * 100))
        : 0,
      countdown: Math.max(0, Number(stats.daysUntilExam) || 0)
    });
  },

  openModule(event) {
    const { page, source } = event.currentTarget.dataset;
    navigation.openPage(`/pages/${page}/index`, source ? { source } : {});
  },

  continueLearning() {
    navigation.continueLearning();
  }
});
