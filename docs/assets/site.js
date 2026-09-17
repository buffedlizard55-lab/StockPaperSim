
// Minimal progressive enhancement: sortable tables and section collapse.
(function () {
  document.querySelectorAll('table.data').forEach(function (tbl) {
    var heads = tbl.querySelectorAll('thead th');
    if (heads.length < 2) return;
    heads.forEach(function (th, idx) {
      th.style.cursor = 'pointer';
      th.title = 'Click to sort';
      th.addEventListener('click', function () {
        var body = tbl.tBodies[0];
        if (!body) return;
        var rows = Array.prototype.slice.call(body.rows);
        var asc = th.dataset.asc !== 'true';
        th.dataset.asc = asc;
        rows.sort(function (a, b) {
          var x = (a.cells[idx] ? a.cells[idx].textContent : '').trim();
          var y = (b.cells[idx] ? b.cells[idx].textContent : '').trim();
          var nx = parseFloat(x.replace(/[^0-9.\-]/g, ''));
          var ny = parseFloat(y.replace(/[^0-9.\-]/g, ''));
          var both = !isNaN(nx) && !isNaN(ny);
          var r = both ? nx - ny : x.localeCompare(y);
          return asc ? r : -r;
        });
        rows.forEach(function (r) { body.appendChild(r); });
      });
    });
  });
})();
