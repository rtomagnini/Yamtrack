(() => {
  const dialog = document.getElementById('youtube-comments-dialog');
  const title = dialog.querySelector('[data-comments-title]');
  const status = dialog.querySelector('[data-comments-status]');
  const content = dialog.querySelector('[data-comments-content]');
  const error = dialog.querySelector('[data-comments-error]');
  const retry = dialog.querySelector('[data-comments-retry]');
  let opener;
  let controller;

  async function load(url, button) {
    const { signal } = controller;
    const restoreFocus = button && document.activeElement === button;
    error.hidden = true;
    retry.hidden = true;
    status.hidden = Boolean(button);
    if (button) button.disabled = true;

    try {
      const response = await fetch(url, { signal, redirect: 'error' });
      const fragment = await response.text();
      if (signal.aborted) return;
      if (!response.ok) {
        error.textContent = response.status === 503
          ? 'Invidious is not configured for this server.'
          : 'Could not load comments. Try again.';
        error.hidden = false;
        retry.hidden = Boolean(button);
        return;
      }

      if (button) {
        const previous = button.previousElementSibling;
        const container = button.parentElement;
        button.insertAdjacentHTML('beforebegin', fragment);
        button.remove();
        if (restoreFocus) {
          const firstNewResult = previous ? previous.nextElementSibling : container.firstElementChild;
          firstNewResult?.setAttribute('tabindex', '-1');
          firstNewResult?.focus({ preventScroll: true });
        }
      } else {
        content.innerHTML = fragment;
      }
    } catch {
      if (signal.aborted) return;
      error.textContent = 'Could not load comments. Try again.';
      error.hidden = false;
      retry.hidden = Boolean(button);
    } finally {
      if (!signal.aborted) status.hidden = true;
      if (button) button.disabled = false;
    }
  }

  document.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-youtube-comments-url]');
    if (!trigger || dialog.open) return;
    opener = trigger;
    controller = new AbortController();
    title.textContent = opener.dataset.videoTitle;
    content.replaceChildren();
    dialog.showModal();
    load(opener.dataset.youtubeCommentsUrl);
  });

  dialog.addEventListener('click', (event) => {
    const more = event.target.closest('[data-comments-more]');
    if (more) load(more.dataset.commentsMore, more);
  });
  retry.addEventListener('click', () => load(opener.dataset.youtubeCommentsUrl));
  dialog.addEventListener('close', () => {
    controller?.abort();
    opener?.focus();
  });
})();
