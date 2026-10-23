/* /body: numbers typed next to the figure.
 *
 * Every input.entry holds the latest saved value. While the person types, the percent next to
 * it is recalculated against data-base (the same baseline the server uses, see summarize() in
 * body.py), the body region of that measurement is recoloured, and the save button is enabled.
 * Only the changed fields are submitted, so nothing unchanged is written to the database.
 */
(() => {
  const form = document.querySelector(".measure-sheet");
  if (!form) return;
  const inputs = [...form.querySelectorAll("input.entry")];
  const save = form.querySelector("#save-measures");

  // Marks an input as changed when its text differs from the saved value and is not empty.
  const initial = new Map(inputs.map((input) => [input, input.value]));
  const isChanged = (input) => input.value.trim() !== "" && input.value !== initial.get(input);

  // Same rules as the server: |pct| < 0.05 or "better" = none means no judgement.
  function tone(pct, better) {
    if (Math.abs(pct) < 0.05 || better === "none") return "flat";
    return (pct > 0) === (better === "up") ? "good" : "bad";
  }

  // Returns [text, tone] for a typed value, or null when it has no baseline to compare with.
  function change(input) {
    const value = parseFloat(input.value.replace(",", "."));
    const base = parseFloat(input.dataset.base);
    if (Number.isNaN(value) || Number.isNaN(base) || base === 0) return null;
    const pct = ((value - base) / base) * 100;
    const text = `(${(pct >= 0 ? "+" : "") + pct.toFixed(1)} %)`.replace("-", "−");
    return [text, tone(pct, input.dataset.better)];
  }

  const original = new Map();
  for (const input of inputs) {
    const label = form.querySelector(`.pct[data-for="${input.name}"]`);
    const region = document.getElementById(`reg-${input.name}`);
    original.set(input, { text: label.textContent, className: label.className, region: region?.getAttribute("class") });

    input.addEventListener("input", () => {
      const result = isChanged(input) ? change(input) : null;
      if (isChanged(input) && result) {
        label.textContent = result[0];
        label.className = `pct tone-${result[1]}`;
        region?.setAttribute("class", `reg-${result[1]}`);
      } else {
        const was = original.get(input);
        label.textContent = isChanged(input) ? "" : was.text;
        label.className = isChanged(input) ? "pct tone-flat" : was.className;
        if (region) was.region ? region.setAttribute("class", was.region) : region.removeAttribute("class");
      }
      input.classList.toggle("changed", isChanged(input));
      save.disabled = !inputs.some(isChanged);
    });
  }

  save.disabled = true;
  form.addEventListener("submit", () => {
    for (const input of inputs) if (!isChanged(input)) input.disabled = true;
  });
})();
