Component({
  properties: {
    title: String,
    region: { type: String, value: '' },
    theme: { type: String, value: 'green' },
    back: { type: Boolean, value: true }
  },
  data: { statusHeight: 24, barHeight: 44, rightSpace: 100 },
  lifetimes: {
    attached() { this.refreshMetrics(); this.updateNavigationColor(); }
  },
  pageLifetimes: {
    show() { this.refreshMetrics(); this.updateNavigationColor(); }
  },
  observers: { theme() { this.updateNavigationColor(); } },
  methods: {
    refreshMetrics() {
      try {
        const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
        const menu = wx.getMenuButtonBoundingClientRect();
        const statusHeight = info.statusBarHeight || 24;
        const validMenu = menu && menu.height > 0 && menu.top >= statusHeight;
        this.setData({
          statusHeight,
          barHeight: validMenu ? (menu.top - statusHeight) * 2 + menu.height : 44,
          rightSpace: validMenu ? info.windowWidth - menu.left + 10 : 100
        });
      } catch (_) { /* 无胶囊信息的预览环境使用基础导航尺寸。 */ }
    },
    updateNavigationColor() {
      if (typeof wx === 'undefined' || !wx.setNavigationBarColor) return;
      const paper = this.data.theme === 'paper';
      wx.setNavigationBarColor({
        frontColor: paper ? '#000000' : '#ffffff',
        backgroundColor: paper ? '#F7F8F5' : '#1B4D3E'
      });
    },
    goBack() {
      const pages = getCurrentPages();
      if (pages.length > 1) wx.navigateBack();
      else wx.reLaunch({ url: '/pages/home/index' });
    }
  }
});
