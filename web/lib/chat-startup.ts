export const chatReadyEvent = "aden:chat-ready";

// Install before hydration so a failed entry script can recover the page.
export const chatStartupScript = `
  (() => {
    const retryKey = "aden-chat-startup-retried";
    const html = document.documentElement;
    let reloading = false;
    const showRetry = () => { html.dataset.chatStartup = "failed"; };
    const timeout = window.setTimeout(showRetry, 10000);

    function onScriptError(event) {
      const script = event.target;
      if (reloading || !(script instanceof HTMLScriptElement) || !script.src) return;
      const url = new URL(script.src);
      if (url.origin !== location.origin || !url.pathname.startsWith("/_next/static/")) return;

      try {
        if (!sessionStorage.getItem(retryKey)) {
          sessionStorage.setItem(retryKey, "1");
          reloading = true;
          location.reload();
          return;
        }
      } catch {}
      showRetry();
    }

    window.addEventListener("error", onScriptError, true);
    window.addEventListener(${JSON.stringify(chatReadyEvent)}, () => {
      window.clearTimeout(timeout);
      window.removeEventListener("error", onScriptError, true);
      delete html.dataset.chatStartup;
      try { sessionStorage.removeItem(retryKey); } catch {}
    }, { once: true });
  })();
`;
