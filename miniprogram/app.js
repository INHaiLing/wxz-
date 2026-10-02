const store = require('./utils/study-store');

App({
  onLaunch() {
    store.getState();
  }
});
