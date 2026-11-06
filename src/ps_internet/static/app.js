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

function paintExcuse(form) {
  const flag = form.querySelector('[name="excused"]');
  const button = form.querySelector("button");
  const on = flag.value === "0";
  button.classList.toggle("is-on", on);
  button.setAttribute("aria-pressed", on ? "true" : "false");
}

function moveExcuse(form) {
  const flag = form.querySelector('[name="excused"]');
  flag.value = flag.value === "1" ? "0" : "1";
  paintExcuse(form);
}

let courseFilter = "";
let worriesOnly = false;

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
    const button = form.querySelector("button");
    button.disabled = true;
    try {
      await postForm(form);
      moveExcuse(form);
      flash("Saved");
    } catch (error) {
      flash(error.message, true);
    } finally {
      button.disabled = false;
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

document.querySelectorAll("form:not([data-live])").forEach(rememberScroll);

const savedY = sessionStorage.getItem("scroll-y");
if (savedY) {
  sessionStorage.removeItem("scroll-y");
  window.scrollTo(0, Number(savedY));
}
