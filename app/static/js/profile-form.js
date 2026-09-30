/*
  Состояния полей формы поискового профиля.

  Механизм ОДИН на все типы полей и не знает ни одного имени поля: он находит
  обёртки `[data-pf-field]`, читает из `data-pf-kind`, по какому правилу считать
  заполненность, и слушает форму целиком через делегирование. Новое поле
  подключается тем же способом — достаточно обернуть его так же, писать сюда
  ничего не нужно.

  Каждая обёртка имеет четыре состояния, и они расставлены по важности:

      error > focused > filled > empty

  * empty / filled — считаются здесь и на сервере по одному и тому же правилу;
  * error          — приходит от браузерной проверки поля (событие `invalid`)
                     или из разметки, если её поставил сервер;
  * focused        — чистый CSS (`:focus`, `:focus-visible`), состоянию обёртки
                     не принадлежит и потому здесь не считается.

  Что пишется в разметку:

      class="is-filled"      — то, чем состояние нарисовано;
      class="has-error"      — то же для ошибки;
      data-pf-state="filled" — то же состояние машиночитаемо (empty/filled/error).

  Всё три выставляет одна функция `applyState`, поэтому разойтись они не могут.

  Начальное состояние приходит с сервера уже в разметке: у профиля, открытого на
  правку, заданные параметры подсвечены до любого взаимодействия, и без этого
  файла форма остаётся правильной — просто перестаёт обновляться до перезагрузки.
*/
(() => {
  const FILLED_CLASS = "is-filled";
  const ERROR_CLASS = "has-error";

  /*
    Значения, которые НЕ считаются заданными. В этом проекте соглашение одно и
    выдержано везде: «не указано» — это пустое value, и у select, и у radio.

    Списка вида none/unknown/not_set здесь нарочно нет. Проверено по разметке:
    в форме языков `none` — это ответ «не знаю совсем», то есть именно сказанное
    человеком значение. Записать его в «не указано» значило бы потерять ответ.
  */
  function isMeaningful(value) {
    return typeof value === "string" && value.trim() !== "";
  }

  function controlsOf(field) {
    // Скрытые маркеры групп сюда не попадают: у них type="hidden".
    return Array.from(field.querySelectorAll("input:not([type=hidden]), select, textarea"));
  }

  /*
    Заполненность по типу поля:

      text     — непустое значение после обрезки пробелов;
      select   — выбран вариант с непустым value («Не указано» имеет пустое);
      choice   — выбран radio с непустым value; «Не указано» выбран, но не задан;
      checkbox — отмечена хотя бы одна настоящая галочка.

    Неизвестный или отсутствующий `data-pf-kind` разбирается по самому контролу —
    чтобы поле, обёрнутое без указания типа, всё равно работало.
  */
  function isFilled(field) {
    const kind = field.dataset.pfKind;

    if (kind === "choice") {
      const checked = field.querySelector("input[type=radio]:checked");
      return checked !== null && isMeaningful(checked.value);
    }
    if (kind === "checkbox") {
      return field.querySelector("input[type=checkbox]:checked") !== null;
    }
    if (kind === "select") {
      const select = field.querySelector("select");
      return select !== null && isMeaningful(select.value);
    }
    if (kind === "text") {
      const control = field.querySelector("input, textarea");
      return control !== null && isMeaningful(control.value);
    }

    return controlsOf(field).some((control) => {
      if (control.type === "checkbox" || control.type === "radio") {
        return control.checked && isMeaningful(control.value);
      }
      return isMeaningful(control.value);
    });
  }

  /*
    Состояние проверки читается СВОЙСТВОМ `validity`, а не вызовом
    `checkValidity()`: вызов сам рассылает событие `invalid`, на которое этот же
    файл и подписан, — и пересчёт состояния уходил бы в бесконечную рекурсию.
    `validity` отвечает то же самое и ничего не рассылает.
  */
  function isBroken(control) {
    return control.validity !== undefined && !control.validity.valid;
  }

  /*
    Ошибка — только та, которую браузер уже показал человеку, то есть после
    попытки отправки. Красить поле красным, пока человек его ещё набирает,
    значит ругаться раньше, чем он закончил.
  */
  function hasError(field) {
    if (field.dataset.pfInvalid === "1") {
      return controlsOf(field).some(isBroken);
    }
    // Ошибку мог поставить сервер — снимать её без повторной проверки нельзя.
    return field.dataset.pfServerError === "1";
  }

  /*
    Текст ошибки — тот, который браузер сам сказал бы во всплывающей подсказке.
    Подсказка исчезает по первому же щелчку, а красная рамка остаётся, и без
    подписи человеку нечего было бы по ней понять.
  */
  function showMessage(field, invalid) {
    const slot = field.querySelector("[data-pf-error]");
    if (!(slot instanceof HTMLElement)) {
      return;
    }
    const broken = invalid ? controlsOf(field).find(isBroken) : undefined;
    slot.textContent = broken ? broken.validationMessage : "";
    slot.hidden = !broken;
  }

  function applyState(field) {
    const filled = isFilled(field);
    const invalid = hasError(field);
    field.classList.toggle(FILLED_CLASS, filled);
    field.classList.toggle(ERROR_CLASS, invalid);
    field.dataset.pfState = invalid ? "error" : filled ? "filled" : "empty";
    showMessage(field, invalid);
  }

  function syncProgress(form) {
    const widget = form.querySelector("[data-pf-progress]");
    if (!(widget instanceof HTMLElement)) {
      return;
    }
    const total = form.querySelectorAll("[data-pf-field]").length;
    const filled = form.querySelectorAll('[data-pf-field][data-pf-state="filled"]').length;

    const filledSlot = widget.querySelector("[data-pf-progress-filled]");
    const totalSlot = widget.querySelector("[data-pf-progress-total]");
    const fill = widget.querySelector("[data-pf-progress-fill]");
    if (filledSlot) {
      filledSlot.textContent = String(filled);
    }
    if (totalSlot) {
      totalSlot.textContent = String(total);
    }
    if (fill instanceof HTMLElement) {
      fill.style.width = total === 0 ? "0%" : Math.round((filled / total) * 100) + "%";
    }
    // Счётчик показывается только когда посчитан: «0 из 0» хуже, чем ничего.
    widget.hidden = total === 0;
  }

  /*
    Подсветка раздела, до которого человек долистал. Полоса наблюдения сдвинута
    вниз от липкой шапки и обрезана сверху от низа окна, чтобы активным считался
    раздел в рабочей части экрана, а не тот, что едва показался снизу.
  */
  function watchSections(form) {
    const links = Array.from(form.querySelectorAll("[data-pf-nav]"));
    if (links.length === 0 || typeof IntersectionObserver === "undefined") {
      return;
    }

    const pairs = [];
    links.forEach((link) => {
      const href = link.getAttribute("href") || "";
      const section = href.startsWith("#") ? form.querySelector(href) : null;
      if (section instanceof HTMLElement) {
        pairs.push({ link: link, section: section });
      }
    });
    if (pairs.length === 0) {
      return;
    }

    const visible = new Set();
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            visible.add(entry.target);
          } else {
            visible.delete(entry.target);
          }
        });
        const current = pairs.find((pair) => visible.has(pair.section));
        pairs.forEach((pair) => {
          pair.link.classList.toggle("is-active", current !== undefined && pair === current);
        });
      },
      { rootMargin: "-100px 0px -55% 0px", threshold: 0 },
    );
    pairs.forEach((pair) => observer.observe(pair.section));
  }

  function fieldsOf(form) {
    return Array.from(form.querySelectorAll("[data-pf-field]"));
  }

  function initForm(form) {
    fieldsOf(form).forEach((field) => {
      // Ошибку, пришедшую из разметки, запоминаем: сама она не пересчитывается.
      if (field.classList.contains(ERROR_CLASS)) {
        field.dataset.pfServerError = "1";
      }
      applyState(field);
    });
    syncProgress(form);
    watchSections(form);

    // `input` покрывает набор текста, `change` — выбор варианта и снятие галочки.
    const onEdit = (event) => {
      const target = event.target;
      if (!(target instanceof Element)) {
        return;
      }
      const field = target.closest("[data-pf-field]");
      if (!(field instanceof HTMLElement)) {
        return;
      }
      applyState(field);
      syncProgress(form);
    };
    form.addEventListener("input", onEdit);
    form.addEventListener("change", onEdit);

    /*
      Браузер шлёт `invalid` каждому непрошедшему проверку полю при отправке.
      С этого момента поле показывает ошибку, пока она не исправлена: дальше её
      снимает обычный пересчёт в `onEdit`.
    */
    form.addEventListener(
      "invalid",
      (event) => {
        const target = event.target;
        if (!(target instanceof Element)) {
          return;
        }
        const field = target.closest("[data-pf-field]");
        if (field instanceof HTMLElement) {
          field.dataset.pfInvalid = "1";
          applyState(field);
        }
      },
      true,
    );
  }

  document.querySelectorAll("[data-pf-form]").forEach((form) => {
    if (form instanceof HTMLFormElement) {
      initForm(form);
    }
  });
})();
