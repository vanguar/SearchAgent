(() => {
  const LOADING_CLASS = "htmx-request";
  let selectedQueryTabKey = null;

  function hasHtmx() {
    return typeof window.htmx !== "undefined";
  }

  function addLoadingState(element) {
    if (element instanceof HTMLElement) {
      element.classList.add(LOADING_CLASS);
    }
  }

  function removeLoadingState(element) {
    if (element instanceof HTMLElement) {
      element.classList.remove(LOADING_CLASS);
    }
  }

  function buildRequestUrl(form) {
    const action = form.getAttribute("action") || window.location.pathname;
    const requestUrl = new URL(action, window.location.origin);
    const formData = new FormData(form);
    requestUrl.search = new URLSearchParams(formData).toString();
    return requestUrl.toString();
  }

  async function fetchHtml(url, options) {
    const response = await fetch(url, {
      credentials: "same-origin",
      headers: {
        "HX-Request": "true",
        "X-Requested-With": "fetch",
      },
      ...options,
    });
    return {
      ok: response.ok,
      body: await response.text(),
    };
  }

  async function loadFragment(container) {
    const loadUrl = container.dataset.loadUrl;
    if (!loadUrl) {
      return;
    }

    addLoadingState(container);
    try {
      const result = await fetchHtml(loadUrl, { method: "GET" });
      if (!result.ok) {
        container.innerHTML = '<div class="notice warning">Не удалось загрузить блок. Попробуйте обновить страницу.</div>';
        return;
      }
      container.innerHTML = result.body;
    } catch (error) {
      console.error("fragment_load_failed", error);
      container.innerHTML = '<div class="notice warning">Не удалось загрузить блок. Попробуйте обновить страницу.</div>';
    } finally {
      removeLoadingState(container);
    }
  }

  async function submitAsyncForm(form) {
    const targetSelector = form.dataset.target;
    if (!targetSelector) {
      return;
    }
    const target = document.querySelector(targetSelector);
    if (!(target instanceof HTMLElement)) {
      return;
    }

    const method = (form.getAttribute("method") || "GET").toUpperCase();
    const requestUrl = method === "GET" ? buildRequestUrl(form) : (form.getAttribute("action") || window.location.pathname);
    const requestOptions =
      method === "GET"
        ? { method }
        : {
            method,
            body: new FormData(form),
          };

    addLoadingState(target);
    try {
      const result = await fetchHtml(requestUrl, requestOptions);
      if (!result.ok) {
        target.innerHTML = '<div class="notice warning">Запрос не выполнился. Попробуйте еще раз.</div>';
        return;
      }
      target.innerHTML = result.body;
    } catch (error) {
      console.error("async_form_submit_failed", error);
      target.innerHTML = '<div class="notice warning">Запрос не выполнился. Проверьте соединение и попробуйте еще раз.</div>';
    } finally {
      removeLoadingState(target);
    }
  }

  function initializeLocalFallbacks() {
    document.querySelectorAll("[data-load-url]").forEach((element) => {
      if (element instanceof HTMLElement) {
        void loadFragment(element);
      }
    });
  }

  function activateQueryTab(button) {
    const root = button.closest("[data-query-tabs]");
    if (!(root instanceof HTMLElement)) {
      return;
    }
    const target = button.dataset.queryTabTarget;
    if (!target) {
      return;
    }
    selectedQueryTabKey = button.dataset.queryTabKey || button.textContent.trim();

    root.querySelectorAll("[data-query-tab-target]").forEach((tab) => {
      if (tab instanceof HTMLElement) {
        const isActive = tab === button;
        tab.classList.toggle("is-active", isActive);
        tab.setAttribute("aria-selected", isActive ? "true" : "false");
      }
    });

    root.querySelectorAll("[data-query-tab-panel]").forEach((panel) => {
      if (panel instanceof HTMLElement) {
        panel.classList.toggle("is-active", panel.dataset.queryTabPanel === target);
      }
    });
  }

  function restoreQueryTabs(root = document) {
    if (!selectedQueryTabKey) {
      return;
    }
    const scope = root instanceof Element || root instanceof Document ? root : document;
    scope.querySelectorAll("[data-query-tabs]").forEach((tabsRoot) => {
      if (!(tabsRoot instanceof HTMLElement)) {
        return;
      }
      const buttons = Array.from(tabsRoot.querySelectorAll("[data-query-tab-target]"));
      const matchingButton = buttons.find((button) => {
        if (!(button instanceof HTMLButtonElement)) {
          return false;
        }
        return (button.dataset.queryTabKey || button.textContent.trim()) === selectedQueryTabKey;
      });
      if (matchingButton instanceof HTMLButtonElement) {
        activateQueryTab(matchingButton);
      }
    });
  }

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    if (!form.matches("[data-async-form]")) {
      return;
    }
    if (hasHtmx()) {
      return;
    }
    event.preventDefault();
    void submitAsyncForm(form);
  });

  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) {
      return;
    }
    const button = target.closest("[data-query-tab-target]");
    if (button instanceof HTMLButtonElement) {
      activateQueryTab(button);
    }
  });

  document.body.addEventListener("htmx:beforeRequest", (event) => {
    const target = event.detail.target;
    addLoadingState(target);
  });

  document.body.addEventListener("htmx:afterSwap", (event) => {
    const target = event.detail.target;
    removeLoadingState(target);
    restoreQueryTabs(document);
    window.setTimeout(() => restoreQueryTabs(document), 0);
  });

  document.body.addEventListener("htmx:afterSettle", () => {
    restoreQueryTabs(document);
  });

  if (!hasHtmx()) {
    initializeLocalFallbacks();
  }
})();
