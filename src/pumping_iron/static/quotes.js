/* Home page: the "Другая цитата" button swaps only the quote section.
 *
 * The link in index.html is a plain `/?shift=N#quote`, which works without a script but reloads
 * the whole page, so the hero photo fades in again. Here the click fetches that page and replaces
 * the #quote section with the new one; the fresh elements replay the word-by-word animation
 * while the photo stays untouched. On any failure the browser follows the link as usual.
 */
(() => {
  document.addEventListener("click", async (event) => {
    const link = event.target.closest("a.quote-next");
    if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) return;
    event.preventDefault();
    try {
      // PERF: fetch a server-rendered fragment of just #quote (own route) instead of the whole page and DOMParser.
      const response = await fetch(link.href);
      if (!response.ok) throw new Error(response.status);
      const next = new DOMParser().parseFromString(await response.text(), "text/html").querySelector("#quote");
      if (!next) throw new Error("no #quote in the response");
      document.querySelector("#quote").replaceWith(next);
      history.replaceState(null, "", link.href);  // a reload keeps the quote that is on screen
    } catch {
      location.href = link.href;
    }
  });
})();
