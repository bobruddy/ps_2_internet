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

function moveExcuse(form) {
  const row = form.closest("li");
  const flag = form.querySelector('[name="excused"]');
  const button = form.querySelector("button");
  const problems = document.querySelector("#problems");
  const excused = document.querySelector("#excused");
  const empty = document.querySelector("#problems-empty");
  const heading = document.querySelector("#excused-heading");
  if (flag.value === "1") {
    flag.value = "0";
    button.textContent = "Count it again";
    excused.appendChild(row);
  } else {
    flag.value = "1";
    button.textContent = "Excuse";
    problems.appendChild(row);
  }
  empty.hidden = problems.children.length > 0;
  heading.hidden = excused.children.length === 0;
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
