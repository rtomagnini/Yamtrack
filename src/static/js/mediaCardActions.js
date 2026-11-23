document.addEventListener('alpine:init', () => {
  Alpine.data('mediaCardActions', () => ({
    actionsOpen: false,
    posterTapped: false,
    posterHovered: false,
    posterPointer: '',
    trackOpen: false,
    listsOpen: false,
    historyOpen: false,

    init() {
      for (const name of ['trackOpen', 'listsOpen', 'historyOpen']) {
        this.$watch(name, open => {
          if (!open) this.$refs.actions.focus({ preventScroll: true });
        });
      }
    },

    handlePosterClick(event) {
      const usesTouch = ['touch', 'pen'].includes(this.posterPointer)
        || matchMedia('(hover: none), (pointer: coarse)').matches;
      if (this.posterTapped || !usesTouch || event.detail === 0
          || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      event.preventDefault();
      this.posterTapped = true;
    },

    dismissPosterActions() {
      this.actionsOpen = false;
      this.posterTapped = false;
    },

    toggleActions() {
      this.actionsOpen = !this.actionsOpen;
      if (this.actionsOpen) this.$nextTick(() => this.$refs.firstAction.focus());
    },

    closeActionsOnEscape(event) {
      if (!this.actionsOpen) return;
      event.stopPropagation();
      this.actionsOpen = false;
      this.$refs.actions.focus();
    },

    openDialog(name) {
      this.actionsOpen = false;
      this[`${name}Open`] = true;
      this.$nextTick(() => this.$refs[`${name}Dialog`].focus());
    },
  }));
});
