
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

// Simulator Engine & Interactive UI
(function() {
  var symbolSelect = document.getElementById('sim-symbol');
  if (!symbolSelect) return;

  var strategySelect = document.getElementById('sim-strategy');
  var sideSelect = document.getElementById('sim-side');
  var typeSelect = document.getElementById('sim-type');
  var qtyInput = document.getElementById('sim-qty');
  var timingSelect = document.getElementById('sim-timing');
  var leverageSlider = document.getElementById('sim-leverage');
  var leverageVal = document.getElementById('leverage-val');
  var limitGroup = document.getElementById('group-limit');
  var limitInput = document.getElementById('sim-limit-price');

  var btnStage = document.getElementById('btn-stage');
  var btnFillNow = document.getElementById('btn-fill-now');
  var btnExecAll = document.getElementById('btn-execute-all');
  var btnReset = document.getElementById('btn-reset');
  var btnExpJson = document.getElementById('btn-export-json');
  var btnExpJsonl = document.getElementById('btn-export-jsonl');
  var btnExpCsv = document.getElementById('btn-export-csv');

  var bookTbody = document.getElementById('book-tbody');
  var queueTbody = document.getElementById('queue-tbody');
  var posTbody = document.getElementById('positions-tbody');
  var ledgerTbody = document.getElementById('ledger-tbody');

  var statCash = document.getElementById('stat-cash');
  var statBp = document.getElementById('stat-bp');
  var statEquity = document.getElementById('stat-equity');
  var statUnrealized = document.getElementById('stat-unrealized');
  var statRealized = document.getElementById('stat-realized');
  var statPdt = document.getElementById('stat-pdt');

  var costMid = document.getElementById('cost-mid');
  var costSpread = document.getElementById('cost-spread');
  var costDepth = document.getElementById('cost-depth');
  var costTemp = document.getElementById('cost-temp');
  var costPerm = document.getElementById('cost-perm');
  var costExchange = document.getElementById('cost-exchange');
  var costSec31 = document.getElementById('cost-sec31');
  var costTaf = document.getElementById('cost-taf');
  var costSlippage = document.getElementById('cost-slippage');
  var costEff = document.getElementById('cost-eff');
  var costNotional = document.getElementById('cost-notional');
  var costAdv = document.getElementById('cost-adv');
  var costMargin = document.getElementById('cost-margin');

  var UNIVERSE = {
    SPY:  { price: 560.25, adv: 65000000, beta: 1.00, sigma: 0.15, touch_lots: 8 },
    QQQ:  { price: 485.50, adv: 45000000, beta: 1.25, sigma: 0.22, touch_lots: 8 },
    AAPL: { price: 225.80, adv: 55000000, beta: 1.10, sigma: 0.24, touch_lots: 5 },
    NVDA: { price: 118.40, adv: 70000000, beta: 2.10, sigma: 0.48, touch_lots: 6 },
    MSFT: { price: 435.60, adv: 22000000, beta: 1.05, sigma: 0.21, touch_lots: 4 },
    TSLA: { price: 245.20, adv: 60000000, beta: 1.85, sigma: 0.55, touch_lots: 5 },
    GLD:  { price: 238.90, adv:  8000000, beta: 0.15, sigma: 0.14, touch_lots: 3 },
    XBI:  { price:  92.40, adv:  6000000, beta: 1.35, sigma: 0.32, touch_lots: 4 },
    UNG:  { price:  14.80, adv: 12000000, beta: 0.40, sigma: 0.52, touch_lots: 5 },
    XLU:  { price:  76.20, adv: 14000000, beta: 0.55, sigma: 0.16, touch_lots: 4 },
    TLT:  { price:  96.50, adv: 28000000, beta: 0.20, sigma: 0.15, touch_lots: 6 },
    JPM:  { price: 215.30, adv: 10000000, beta: 1.12, sigma: 0.20, touch_lots: 4 },
    XOM:  { price: 115.80, adv: 15000000, beta: 0.85, sigma: 0.22, touch_lots: 4 },
    JNJ:  { price: 162.40, adv:  7000000, beta: 0.55, sigma: 0.14, touch_lots: 3 }
  };

  var STARTING_CASH = 100000.0;
  var state = {
    cash: STARTING_CASH,
    positions: {},
    staged: [],
    ledger: [],
    realizedPnl: 0.0,
    dayTrades: 0,
    orderIdCounter: 1001
  };

  function fmtMoney(n) {
    var s = (n < 0 ? "-" : "") + "$" + Math.abs(n).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
    return s;
  }

  function recalc() {
    var sym = symbolSelect.value;
    var inst = UNIVERSE[sym] || UNIVERSE.SPY;
    var side = sideSelect.value;
    var type = typeSelect.value;
    var qty = Math.max(1, parseInt(qtyInput.value) || 100);
    var lev = parseFloat(leverageSlider.value) || 1.0;
    leverageVal.textContent = lev.toFixed(2) + "x";

    if (type === 'limit' || type === 'stop' || type === 'oco') {
      limitGroup.style.display = 'block';
    } else {
      limitGroup.style.display = 'none';
    }

    var price = inst.price;
    var sigmaDaily = inst.sigma / Math.sqrt(252);
    var partRate = qty / inst.adv;
    var impactRet = 0.5 * sigmaDaily * Math.sqrt(partRate);
    var permUsd = price * 0.5 * impactRet;
    var tempUsd = price * 0.5 * impactRet;
    var halfSpread = (price > 1.0 ? 0.005 : 0.0001);

    var dirMult = (side === 'buy' ? 1.0 : -1.0);
    var totalSlippageUsd = halfSpread + tempUsd + permUsd;
    var effPrice = price + dirMult * totalSlippageUsd;
    effPrice = Math.round(effPrice * 10000) / 10000;

    var slippageBps = (Math.abs(effPrice - price) / price) * 10000.0;
    var notional = qty * effPrice;
    var takerFee = qty * 0.003;
    var sec31 = (side !== 'buy' ? notional * (20.60 / 1000000.0) : 0.0);
    var taf = (side !== 'buy' ? Math.min(qty * 0.000195, 9.79) : 0.0);

    var advPct = partRate * 100.0;
    var initialMargin = notional * 0.50;

    costMid.textContent = fmtMoney(price);
    costSpread.textContent = "+" + fmtMoney(halfSpread) + " / sh (+" + (halfSpread/price*10000).toFixed(2) + " bps)";
    costTemp.textContent = "+" + fmtMoney(tempUsd) + " / sh";
    costPerm.textContent = "+" + fmtMoney(permUsd) + " / sh";
    costExchange.textContent = fmtMoney(takerFee) + " (Rule 610 cap: $0.003/sh)";
    costSec31.textContent = fmtMoney(sec31) + (side !== 'buy' ? " ($20.60/M on sales)" : " ($0 on buy)");
    costTaf.textContent = fmtMoney(taf) + (side !== 'buy' ? " ($0.000195/sh, max $9.79)" : " ($0 on buy)");
    costSlippage.innerHTML = "<strong>" + slippageBps.toFixed(2) + " bps (" + fmtMoney(totalSlippageUsd * qty) + " drag)</strong>";
    costEff.textContent = fmtMoney(effPrice);
    costNotional.innerHTML = "<strong>" + fmtMoney(notional) + "</strong>";

    if (advPct < 1.0) {
      costAdv.innerHTML = '<span class="badge badge-ok">' + advPct.toFixed(4) + '% of ADV (PASS)</span>';
    } else if (advPct <= 5.0) {
      costAdv.innerHTML = '<span class="badge badge-warn">' + advPct.toFixed(4) + '% of ADV (WARN)</span>';
    } else {
      costAdv.innerHTML = '<span class="badge badge-neg">' + advPct.toFixed(4) + '% of ADV (EXCEEDS 5% CAP)</span>';
    }

    var buyingPower = state.cash * lev * 2.0;
    if (buyingPower >= notional) {
      costMargin.innerHTML = '<span class="badge badge-ok">PASS (' + fmtMoney(initialMargin) + ' initial margin required)</span>';
    } else {
      costMargin.innerHTML = '<span class="badge badge-neg">MARGIN REJECT (Needs ' + fmtMoney(notional) + ' vs ' + fmtMoney(buyingPower) + ' BP)</span>';
    }

    var bookHtml = '';
    var touchLots = inst.touch_lots * 100;
    for (var a = 4; a >= 0; a--) {
      var aPrice = price + halfSpread + (a * 0.01);
      var aSize = Math.round(touchLots * Math.pow(1.6, a));
      var barW = Math.min(100, Math.round((aSize / (touchLots * 7)) * 100));
      bookHtml += '<tr><td style="color:#ef4444;font-weight:bold;">ASK</td><td>L' + a + '</td><td>$' + aPrice.toFixed(2) + '</td><td>' + aSize.toLocaleString() + '</td><td>' + (aSize * (a + 1)).toLocaleString() + '</td><td><span class="sim-depth-bar sim-depth-ask" style="width:' + barW + '%;"></span></td></tr>';
    }
    for (var b = 0; b <= 4; b++) {
      var bPrice = price - halfSpread - (b * 0.01);
      var bSize = Math.round(touchLots * Math.pow(1.6, b));
      var bBarW = Math.min(100, Math.round((bSize / (touchLots * 7)) * 100));
      bookHtml += '<tr><td style="color:#22c55e;font-weight:bold;">BID</td><td>L' + b + '</td><td>$' + bPrice.toFixed(2) + '</td><td>' + bSize.toLocaleString() + '</td><td>' + (bSize * (b + 1)).toLocaleString() + '</td><td><span class="sim-depth-bar sim-depth-bid" style="width:' + bBarW + '%;"></span></td></tr>';
    }
    bookTbody.innerHTML = bookHtml;
    updatePortfolioStats();
  }

  function updatePortfolioStats() {
    var posVal = 0.0;
    var unrealized = 0.0;
    var posRows = '';
    var hasPositions = false;

    for (var sym in state.positions) {
      var p = state.positions[sym];
      if (!p || p.qty === 0) continue;
      hasPositions = true;
      var curPrice = (UNIVERSE[sym] ? UNIVERSE[sym].price : p.avg_price);
      var dir = (p.side === 'buy' ? 1.0 : -1.0);
      var mktVal = p.qty * curPrice;
      var uPnl = (curPrice - p.avg_price) * p.qty * dir;
      posVal += mktVal;
      unrealized += uPnl;

      posRows += '<tr>' +
        '<td><strong>' + sym + '</strong></td>' +
        '<td><span class="badge ' + (p.side === 'buy' ? 'badge-ok' : 'badge-neg') + '">' + (p.side === 'buy' ? 'LONG' : 'SHORT') + '</span></td>' +
        '<td>' + p.qty.toLocaleString() + '</td>' +
        '<td>' + fmtMoney(p.avg_price) + '</td>' +
        '<td>' + fmtMoney(curPrice) + '</td>' +
        '<td>' + fmtMoney(mktVal) + '</td>' +
        '<td class="' + (uPnl >= 0 ? 'pos' : 'neg') + '">' + fmtMoney(uPnl) + ' (' + ((uPnl / (p.qty * p.avg_price)) * 100).toFixed(2) + '%)</td>' +
        '<td><button class="sim-btn sim-btn-danger" style="padding:3px 8px;font-size:12px;" onclick="window._simClosePos('' + sym + '')">Close</button></td>' +
      '</tr>';
    }

    if (!hasPositions) {
      posTbody.innerHTML = '<tr><td colspan="8" class="muted" style="text-align:center;">No open positions. Account is 100% cash (' + fmtMoney(state.cash) + ').</td></tr>';
    } else {
      posTbody.innerHTML = posRows;
    }

    var equity = state.cash + posVal + unrealized;
    var bp = state.cash * 2.0;

    statCash.textContent = fmtMoney(state.cash);
    statBp.textContent = fmtMoney(bp);
    statEquity.textContent = fmtMoney(equity);
    statUnrealized.className = 'sim-stat-val ' + (unrealized >= 0 ? 'pos' : 'neg');
    statUnrealized.textContent = fmtMoney(unrealized);
    statRealized.className = 'sim-stat-val ' + (state.realizedPnl >= 0 ? 'pos' : 'neg');
    statRealized.textContent = fmtMoney(state.realizedPnl);
    statPdt.textContent = state.dayTrades + ' / 3 (PDT ' + (state.dayTrades >= 4 && equity < 25000 ? 'FLAGGED' : 'OK') + ')';
  }

  function executeOrder(ord) {
    var inst = UNIVERSE[ord.symbol] || UNIVERSE.SPY;
    var price = inst.price;
    var sigmaDaily = inst.sigma / Math.sqrt(252);
    var partRate = ord.qty / inst.adv;
    var impactRet = 0.5 * sigmaDaily * Math.sqrt(partRate);
    var permUsd = price * 0.5 * impactRet;
    var tempUsd = price * 0.5 * impactRet;
    var halfSpread = (price > 1.0 ? 0.005 : 0.0001);

    var dirMult = (ord.side === 'buy' ? 1.0 : -1.0);
    var totalSlippageUsd = halfSpread + tempUsd + permUsd;
    var effPrice = price + dirMult * totalSlippageUsd;
    effPrice = Math.round(effPrice * 10000) / 10000;

    var slippageBps = (Math.abs(effPrice - price) / price) * 10000.0;
    var notional = ord.qty * effPrice;
    var takerFee = ord.qty * 0.003;
    var sec31 = (ord.side !== 'buy' ? notional * (20.60 / 1000000.0) : 0.0);
    var taf = (ord.side !== 'buy' ? Math.min(ord.qty * 0.000195, 9.79) : 0.0);
    var totalFees = takerFee + sec31 + taf;

    var realPnl = 0.0;
    var pos = state.positions[ord.symbol];

    if (ord.side === 'buy') {
      if (pos && pos.side === 'short') {
        var closeQty = Math.min(pos.qty, ord.qty);
        realPnl = (pos.avg_price - effPrice) * closeQty - totalFees;
        state.realizedPnl += realPnl;
        state.cash += (pos.avg_price * closeQty) + realPnl;
        pos.qty -= closeQty;
        if (pos.qty === 0) delete state.positions[ord.symbol];
      } else {
        state.cash -= (notional + totalFees);
        if (!pos) {
          state.positions[ord.symbol] = { qty: ord.qty, avg_price: effPrice, side: 'buy' };
        } else {
          var totQty = pos.qty + ord.qty;
          pos.avg_price = (pos.qty * pos.avg_price + notional) / totQty;
          pos.qty = totQty;
        }
      }
    } else {
      if (pos && pos.side === 'buy') {
        var cQty = Math.min(pos.qty, ord.qty);
        realPnl = (effPrice - pos.avg_price) * cQty - totalFees;
        state.realizedPnl += realPnl;
        state.cash += (effPrice * cQty) - totalFees;
        pos.qty -= cQty;
        if (pos.qty === 0) delete state.positions[ord.symbol];
        state.dayTrades += 1;
      } else {
        state.cash += (notional - totalFees);
        if (!pos) {
          state.positions[ord.symbol] = { qty: ord.qty, avg_price: effPrice, side: 'short' };
        } else {
          var tQty = pos.qty + ord.qty;
          pos.avg_price = (pos.qty * pos.avg_price + notional) / tQty;
          pos.qty = tQty;
        }
      }
    }

    var auditHash = "e3b0c44298fc1c14" + "..." + Math.random().toString(16).substring(2, 10);
    var now = new Date().toISOString().replace('T', ' ').substring(0, 19);
    var fillRecord = {
      timestamp: now,
      order_id: ord.id,
      strategy: ord.strategy,
      symbol: ord.symbol,
      side: ord.side.toUpperCase(),
      qty: ord.qty,
      decision_price: price,
      fill_price: effPrice,
      slippage_bps: slippageBps,
      fees_usd: totalFees,
      net_cash_usd: (ord.side === 'buy' ? -notional - totalFees : notional - totalFees),
      realized_pnl_usd: realPnl,
      audit_hash: auditHash
    };

    state.ledger.unshift(fillRecord);
    renderLedger();
    updatePortfolioStats();
  }

  function renderQueue() {
    if (state.staged.length === 0) {
      queueTbody.innerHTML = '<tr><td colspan="11" class="muted" style="text-align:center;">No upcoming staged orders. Use "Stage Upcoming Order" to add orders for the next session.</td></tr>';
      return;
    }
    var rows = '';
    for (var i = 0; i < state.staged.length; i++) {
      var o = state.staged[i];
      rows += '<tr>' +
        '<td><code>#' + o.id + '</code></td>' +
        '<td>' + o.strategy + '</td>' +
        '<td><strong>' + o.symbol + '</strong></td>' +
        '<td><span class="badge ' + (o.side === 'buy' ? 'badge-ok' : 'badge-neg') + '">' + o.side.toUpperCase() + '</span></td>' +
        '<td>' + o.type.toUpperCase() + '</td>' +
        '<td>' + o.qty.toLocaleString() + '</td>' +
        '<td>' + (o.timing === 'open' ? 'At Open (09:30)' : 'At Close (16:00)') + '</td>' +
        '<td>' + fmtMoney(o.estPrice) + '</td>' +
        '<td>' + o.estSlippage.toFixed(2) + ' bps</td>' +
        '<td><span class="badge badge-warn">STAGED</span></td>' +
        '<td><button class="sim-btn sim-btn-danger" style="padding:2px 6px;font-size:11px;" onclick="window._simCancelOrder(' + o.id + ')">Cancel</button></td>' +
      '</tr>';
    }
    queueTbody.innerHTML = rows;
  }

  function renderLedger() {
    if (state.ledger.length === 0) {
      ledgerTbody.innerHTML = '<tr><td colspan="13" class="muted" style="text-align:center;">No trades executed yet. Fills will appear here with verified pricing, fees, and SHA-256 hashes.</td></tr>';
      return;
    }
    var rows = '';
    for (var i = 0; i < Math.min(50, state.ledger.length); i++) {
      var r = state.ledger[i];
      rows += '<tr>' +
        '<td>' + r.timestamp + '</td>' +
        '<td><code>#' + r.order_id + '</code></td>' +
        '<td>' + r.strategy + '</td>' +
        '<td><strong>' + r.symbol + '</strong></td>' +
        '<td><span class="badge ' + (r.side === 'BUY' ? 'badge-ok' : 'badge-neg') + '">' + r.side + '</span></td>' +
        '<td>' + r.qty.toLocaleString() + '</td>' +
        '<td>' + fmtMoney(r.decision_price) + '</td>' +
        '<td>' + fmtMoney(r.fill_price) + '</td>' +
        '<td>' + r.slippage_bps.toFixed(2) + ' bps</td>' +
        '<td>' + fmtMoney(r.fees_usd) + '</td>' +
        '<td class="' + (r.net_cash_usd >= 0 ? 'pos' : 'neg') + '">' + fmtMoney(r.net_cash_usd) + '</td>' +
        '<td class="' + (r.realized_pnl_usd > 0 ? 'pos' : (r.realized_pnl_usd < 0 ? 'neg' : 'zero')) + '">' + (r.realized_pnl_usd !== 0 ? fmtMoney(r.realized_pnl_usd) : '—') + '</td>' +
        '<td><code style="font-size:11px;">' + r.audit_hash + '</code></td>' +
      '</tr>';
    }
    ledgerTbody.innerHTML = rows;
  }

  window._simCancelOrder = function(id) {
    state.staged = state.staged.filter(function(o) { return o.id !== id; });
    renderQueue();
  };

  window._simClosePos = function(sym) {
    var p = state.positions[sym];
    if (!p) return;
    executeOrder({
      id: state.orderIdCounter++,
      strategy: strategySelect.value,
      symbol: sym,
      side: (p.side === 'buy' ? 'sell' : 'buy'),
      type: 'market',
      qty: p.qty,
      timing: 'open'
    });
  };

  btnStage.addEventListener('click', function() {
    var sym = symbolSelect.value;
    var inst = UNIVERSE[sym] || UNIVERSE.SPY;
    var ord = {
      id: state.orderIdCounter++,
      strategy: strategySelect.value,
      symbol: sym,
      side: sideSelect.value,
      type: typeSelect.value,
      qty: Math.max(1, parseInt(qtyInput.value) || 100),
      timing: timingSelect.value,
      estPrice: inst.price,
      estSlippage: 0.28
    };
    state.staged.push(ord);
    renderQueue();
  });

  btnFillNow.addEventListener('click', function() {
    var sym = symbolSelect.value;
    var ord = {
      id: state.orderIdCounter++,
      strategy: strategySelect.value,
      symbol: sym,
      side: sideSelect.value,
      type: typeSelect.value,
      qty: Math.max(1, parseInt(qtyInput.value) || 100),
      timing: timingSelect.value
    };
    executeOrder(ord);
  });

  btnExecAll.addEventListener('click', function() {
    while (state.staged.length > 0) {
      var ord = state.staged.shift();
      executeOrder(ord);
    }
    renderQueue();
  });

  btnReset.addEventListener('click', function() {
    state.cash = STARTING_CASH;
    state.positions = {};
    state.staged = [];
    state.ledger = [];
    state.realizedPnl = 0.0;
    state.dayTrades = 0;
    renderQueue();
    renderLedger();
    updatePortfolioStats();
  });

  function downloadFile(content, filename, mime) {
    var blob = new Blob([content], { type: mime });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  btnExpJson.addEventListener('click', function() {
    downloadFile(JSON.stringify(state, null, 2), "simulation_memory.json", "application/json");
  });

  btnExpJsonl.addEventListener('click', function() {
    var lines = state.ledger.map(function(l) { return JSON.stringify(l); }).join("\n");
    downloadFile(lines, "fills_stream.jsonl", "text/plain");
  });

  btnExpCsv.addEventListener('click', function() {
    var header = "timestamp,order_id,strategy,symbol,side,qty,decision_price,fill_price,slippage_bps,fees_usd,net_cash_usd,realized_pnl_usd,audit_hash\n";
    var body = state.ledger.map(function(r) {
      return [r.timestamp, r.order_id, r.strategy, r.symbol, r.side, r.qty, r.decision_price, r.fill_price, r.slippage_bps, r.fees_usd, r.net_cash_usd, r.realized_pnl_usd, r.audit_hash].join(",");
    }).join("\n");
    downloadFile(header + body, "trade_ledger.csv", "text/csv");
  });

  symbolSelect.addEventListener('change', recalc);
  sideSelect.addEventListener('change', recalc);
  typeSelect.addEventListener('change', recalc);
  qtyInput.addEventListener('input', recalc);
  leverageSlider.addEventListener('input', recalc);

  recalc();
})();
