const utils = require('../../utils/question-utils');

Component({
  properties: {
    question: { type: Object, value: {} },
    mode: { type: String, value: 'writing' },
    number: { type: Number, value: 1 },
    variant: { type: String, value: 'green' },
    showTag: { type: Boolean, value: false }
  },
  data: { segments: [], isTranslation: false, translationStem: '', translationAnswer: '' },
  observers: {
    'question, mode'(question, mode) {
      const item = question || {};
      const stem = String(item.stem || '');
      const isTranslation = item.type === 'translation';
      const quoted = /^[“「『"]([\s\S]+?)[”」』"]/.exec(stem);
      const translationStem = isTranslation
        ? (quoted ? quoted[1] : stem.split(/\{\{\d+\}\}/)[0].replace(/(?:翻译为|译为|翻译)[：:\s]*$/, ''))
        : '';
      const answers = Array.isArray(item.answers) ? item.answers : [];
      this.setData({
        segments: utils.segments(stem, answers, mode).map((part) => {
          const text = part.isAnswer ? part.text.slice(1, -1) : part.text;
          return { ...part, text, compactAnswer: (part.isAnswer && text.length <= 8) || text === '____' };
        }),
        isTranslation,
        translationStem,
        translationAnswer: isTranslation && mode === 'reciting' ? answers.filter(Boolean).join('；') : ''
      });
    }
  },
  methods: {
    favorite() { this.triggerEvent('favorite', { id: this.data.question.id }); },
    master() { this.triggerEvent('master', { id: this.data.question.id }); }
  }
});
