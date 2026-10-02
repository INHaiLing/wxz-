Component({
  properties: { name: { type: String, value: 'book' }, size: { type: Number, value: 52 }, color: { type: String, value: '' } },
  data: { iconSource: '/assets/icons/book.png' },
  observers: {
    'name, color'(name, color) {
      const variants = { '#1B4D3E': 'green', '#C47A2B': 'amber', '#FFFFFF': 'white', '#5C6460': 'muted' };
      const suffix = variants[String(color || '').toUpperCase()];
      this.setData({ iconSource: '/assets/icons/' + (name || 'book') + (suffix ? '-' + suffix : '') + '.png' });
    }
  }
});
