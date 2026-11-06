const toast = document.querySelector("#toast");
let toastTimer = 0;

function flash(text, bad) {
  if (!toast) return;
  toast.textContent = text;
  toast.classList.toggle("bad", Boolean(bad));
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 1600);
}

function rememberScroll(form) {
  form.addEventListener("submit", () => {
    sessionStorage.setItem("scroll-y", String(window.scrollY));
  });
}

async function postForm(form) {
  const response = await fetch(form.action, {
    method: "POST",
    body: new FormData(form),
    headers: { Accept: "application/json" },
  });
  const type = response.headers.get("content-type") || "";
  if (!type.includes("application/json")) {
    throw new Error("Sign in again");
  }
  const data = await response.json();
  if (!response.ok || !data.ok) {
    throw new Error(data.error || "Could not save");
  }
  return data;
}

function paintExcuse(form, data) {
  const forever = form.querySelector('button[data-choice="forever"]');
  const held = Boolean(data.excused);
  const temporary = Boolean(data.until);
  forever.classList.toggle("is-on", held && !temporary);
  forever.setAttribute("aria-pressed", held && !temporary ? "true" : "false");
  let clear = form.querySelector('button[data-choice="clear"]');
  if (temporary) {
    if (!clear) {
      clear = document.createElement("button");
      clear.type = "submit";
      clear.className = "excuse is-on until";
      clear.dataset.choice = "clear";
      form.appendChild(clear);
    }
    clear.textContent = data.until_short || data.until;
    clear.title = "Until " + data.until + ". Click to clear.";
  } else if (clear) {
    clear.remove();
  }
}

let courseFilter = "";
let worriesOnly = true;

function courseName(node) {
  const label = node.querySelector(".course");
  return (label ? label.textContent : node.getAttribute("data-course") || "").replace(/\s+/g, " ").trim();
}

function applyAssignmentFilters() {
  const list = document.querySelector("#assignments");
  if (!list) return;
  list.classList.toggle("quiet", worriesOnly);
  const rows = list.querySelectorAll(":scope > li");
  let shown = 0;
  rows.forEach((row) => {
    const courseOk = !courseFilter || courseName(row) === courseFilter;
    const ungraded = row.getAttribute("data-ungraded") === "1";
    const worryOk = !worriesOnly || (row.getAttribute("data-counts") === "1" && !ungraded);
    const visible = courseOk && worryOk;
    row.hidden = !visible;
    if (visible) shown += 1;
  });
  const empty = document.querySelector("#assignments-empty");
  if (empty) empty.hidden = shown > 0;
  document.querySelectorAll(".course-filter").forEach((card) => {
    const on = courseName(card) === courseFilter && courseFilter !== "";
    card.classList.toggle("is-selected", on);
    card.setAttribute("aria-pressed", on ? "true" : "false");
  });
}

document.querySelectorAll("form[data-live='excuse']").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.submitter || form.querySelector("button");
    let choice = button.dataset.choice || button.value || "forever";
    if (choice === "forever" && button.classList.contains("is-on")) choice = "clear";
    let field = form.querySelector('input[name="choice"]');
    if (!field) {
      field = document.createElement("input");
      field.type = "hidden";
      field.name = "choice";
      form.appendChild(field);
    }
    field.value = choice;
    const controls = form.querySelectorAll("button");
    controls.forEach((item) => {
      item.disabled = true;
    });
    try {
      const saved = await postForm(form);
      paintExcuse(form, saved);
      flash("Saved");
    } catch (error) {
      flash(error.message, true);
    } finally {
      controls.forEach((item) => {
        item.disabled = false;
      });
    }
  });
});

document.querySelectorAll(".course-filter").forEach((card) => {
  card.addEventListener("click", () => {
    const name = courseName(card);
    courseFilter = courseFilter === name ? "" : name;
    applyAssignmentFilters();
  });
});

const onlyCounts = document.querySelector("#only-counts");
if (onlyCounts) {
  onlyCounts.addEventListener("click", () => {
    worriesOnly = !worriesOnly;
    onlyCounts.classList.toggle("is-on", worriesOnly);
    onlyCounts.setAttribute("aria-pressed", worriesOnly ? "true" : "false");
    applyAssignmentFilters();
  });
}

document.querySelectorAll("form[data-live='setting']").forEach((form) => {
  form.addEventListener("change", async () => {
    form.classList.add("saving");
    try {
      await postForm(form);
      flash("Saved");
    } catch (error) {
      flash(error.message, true);
    } finally {
      form.classList.remove("saving");
    }
  });
});

document.querySelectorAll("form[data-live='rule']").forEach((form) => {
  let chain = Promise.resolve();
  form.addEventListener("change", () => {
    chain = chain.then(async () => {
      form.classList.add("saving");
      try {
        await postForm(form);
        flash("Saved");
      } catch (error) {
        flash(error.message, true);
      } finally {
        form.classList.remove("saving");
      }
    });
  });
});

function copyWithCommand(text) {
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.left = "-9999px";
  document.body.appendChild(area);
  area.select();
  const ok = document.execCommand("copy");
  area.remove();
  if (!ok) {
    throw new Error("copy failed");
  }
}

async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch (error) {
      copyWithCommand(text);
      return;
    }
  }
  copyWithCommand(text);
}

document.querySelectorAll(".copy-link").forEach((button) => {
  button.addEventListener("click", async () => {
    const text = (button.dataset.copy || button.textContent || "").trim();
    try {
      await copyText(text);
      flash("Copied");
    } catch (error) {
      flash("Could not copy", true);
    }
  });
});

document.querySelectorAll("form:not([data-live])").forEach(rememberScroll);

applyAssignmentFilters();

const savedY = sessionStorage.getItem("scroll-y");
if (savedY) {
  sessionStorage.removeItem("scroll-y");
  window.scrollTo(0, Number(savedY));
}
