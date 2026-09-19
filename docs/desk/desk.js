/* No price input, local paper fills, external scripts or browser credentials. */
'use strict';
const search = document.getElementById('search');
const status = document.getElementById('status');
const cards = [...document.querySelectorAll('.strategy')];
function filterDesk() {
  const query = search.value.trim().toLocaleLowerCase();
  let visible = 0;
  cards.forEach(card => {
    const match = card.dataset.searchText.toLocaleLowerCase().includes(query) &&
      (!status.value || card.dataset.status === status.value);
    card.hidden = !match;
    if (match) visible += 1;
  });
  document.querySelectorAll('table[data-searchable] tbody tr').forEach(row => {
    row.hidden = !row.textContent.toLocaleLowerCase().includes(query);
  });
  document.getElementById('result-count').textContent = `${visible} of ${cards.length} strategies · text search also filters journal and sources`;
  document.getElementById('empty').hidden = visible !== 0;
}
search.addEventListener('input', filterDesk);
status.addEventListener('change', filterDesk);
document.getElementById('reset').addEventListener('click', () => {
  search.value = ''; status.value = ''; filterDesk(); search.focus();
});
