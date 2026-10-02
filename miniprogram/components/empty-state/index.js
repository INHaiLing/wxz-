Component({
  properties: {
    title: { type: String, value: '暂无演示内容' },
    description: { type: String, value: '题库后续补充，先去其他分类看看吧' },
    actionText: { type: String, value: '' }
  },
  methods: { action() { this.triggerEvent('action'); } }
});
