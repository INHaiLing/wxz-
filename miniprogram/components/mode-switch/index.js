Component({
  properties: { mode: { type: String, value: 'writing' }, theme: { type: String, value: 'green' } },
  methods: { change(event) { this.triggerEvent('change', { mode: event.currentTarget.dataset.mode }); } }
});
