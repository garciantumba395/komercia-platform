/* Komersia – Caisse. Écran pensé pour aller vite : produits -> panier et paiement -> reçu (3 écrans). */
(function () {
  'use strict';

  const K = window.Komersia;
  const { h, clear, field, fmt, parseAmount, toInput, convert, paidInCurrency, fmtQty, api, toast, busy } = K;

  const MAX_TILES_PAGE = 50;

  function uuid() {
    if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
    // Repli pour les anciens navigateurs (Android 8) : UUID v4 à partir de getRandomValues.
    const b = new Uint8Array(16);
    window.crypto.getRandomValues(b);
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    const x = Array.from(b, (v) => v.toString(16).padStart(2, '0')).join('');
    return `${x.slice(0, 8)}-${x.slice(8, 12)}-${x.slice(12, 16)}-${x.slice(16, 20)}-${x.slice(20)}`;
  }

  const divRound = (n, d) => (n * 2n + d) / (2n * d);

  /** Quantité saisie (« 2 », « 0,5 ») -> millièmes (BigInt) ou null si invalide / nulle. */
  function qtyMilli(text) {
    const s = String(text).trim().replace(',', '.');
    if (!/^\d+(\.\d{1,3})?$/.test(s)) return null;
    const [i, f = ''] = s.split('.');
    const v = BigInt(i) * 1000n + BigInt((f + '000').slice(0, 3));
    return v > 0n ? v : null;
  }
  const normQty = (text) => String(text).trim().replace(',', '.');

  function errorBox(message) { return K.alertBox(message, 'error'); }

  K.pages.pos = async function pos(view, ctx) {
    const S = {
      ctx,
      session: null,
      cart: [],
      currency: (ctx.tenant && ctx.tenant.base_currency) || 'CDF',
      customer: null,
      mode: 'cash',
      discount: '',
      cdf: '',
      usd: '',
      changeCur: null,
      clientId: null,
      submitting: false,
      stock: {},
      products: [],
      nextUrl: null,
      query: '',
      category: '',
      searchSeq: 0,
    };
    S.changeCur = S.currency;

    /* ----- 1. Taux de change obligatoire ----- */
    if (!ctx.rate) return renderRateSetup();
    return checkSession();

    function renderRateSetup() {
      clear(view);
      view.appendChild(K.alertBox('Aucun taux de change n’est défini. Il faut le saisir avant la première vente.', 'warn'));
      if (!K.canWrite.rates(ctx.role)) {
        view.appendChild(h('p', null, 'Demandez au propriétaire ou au gérant de saisir le taux (menu « Taux de change »).'));
        return null;
      }
      view.appendChild(h('p', { class: 'hint' }, 'Vous pouvez aussi le gérer dans la page ', h('a', { href: '/app/taux/' }, 'Taux de change'), '.'));
      const rate = h('input', { type: 'text', inputmode: 'decimal', placeholder: '2800', required: true });
      const err = h('div');
      const btn = h('button', { type: 'submit', class: 'btn btn-big' }, 'Enregistrer le taux');
      const form = h('form', { class: 'card' }, h('h2', null, 'Taux du jour'), err,
        field('1 dollar vaut combien de francs (FC) ?', rate, 'Exemple : 2800'), btn);
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        clear(err);
        const value = rate.value.trim().replace(',', '.');
        if (!/^\d+(\.\d{1,4})?$/.test(value) || Number(value) <= 0) { err.appendChild(errorBox('Saisissez un taux valide, par exemple 2800.')); return; }
        await busy(btn, async () => {
          try {
            await api('currency/rates/', { method: 'POST', body: { rate_cdf_per_usd: value, store: null } });
            await K.refreshRate();
            toast('Taux enregistré.', 'ok');
            S.ctx.rate = K.ctx.rate;
            S.ctx.rateScaled = K.ctx.rateScaled;
            checkSession();
          } catch (ex) { err.appendChild(errorBox(ex.message)); }
        });
      });
      view.appendChild(form);
      return null;
    }

    /* ----- 2. Session de caisse ----- */
    /** Message d'abonnement terminé, adapté au rôle (seul le propriétaire peut s'abonner). */
    function roText() {
      return ctx.role === 'owner'
        ? 'Abonnement terminé : l’encaissement est désactivé. Abonnez-vous pour reprendre les ventes.' : K.billing.INACTIVE_MESSAGE;
    }

    /** Abonnement en lecture seule : pas de nouvelle vente (le serveur la refuserait de toute façon, code 402). */
    function renderReadOnly() {
      clear(view).append(h('section', { class: 'card ro-card' },
        K.alertBox(roText(), 'error'),
        h('p', null, 'Vos données sont conservées. Les ventes déjà faites restent consultables et leurs reçus imprimables depuis « Ventes du jour ».'),
        h('div', { class: 'btn-row', style: 'justify-content:center' },
          h('a', { class: 'btn', href: '/app/ventes/' }, 'Voir les ventes du jour'),
          ctx.role === 'owner' ? h('a', { class: 'btn btn-secondary', href: '/app/abonnement/' }, 'S’abonner') : null)));
      return null;
    }

    async function checkSession() {
      if (K.billing.isReadOnly()) return renderReadOnly();
      clear(view).appendChild(h('p', null, 'Chargement de la caisse…'));
      try {
        S.session = await currentSession();
        if (!S.session) return renderOpenSession();
      } catch (e) {
        if (e instanceof K.ApiError && e.status === 404 && e.data) { S.session = null; return renderOpenSession(); }
        clear(view).append(
          errorBox(e.status === 404 ? 'Le module des ventes n’est pas encore disponible sur ce serveur.' : e.message),
          h('button', { type: 'button', class: 'btn', onclick: checkSession }, 'Réessayer'));
        return null;
      }
      return startPos();
    }

    /** Session ouverte de l'utilisateur : la liste évite le 404 (bruit en console) quand aucune caisse n'est ouverte. */
    async function currentSession() {
      try {
        const data = await api('sales/cash-sessions/?status=open&store=' + encodeURIComponent(ctx.store.id));
        const rows = Array.isArray(data) ? data : data.results || [];
        return rows.find((x) => x.status === 'open' && (!ctx.me || x.cashier === ctx.me.id)) || null;
      } catch (e) {
        if (e instanceof K.ApiError && (e.status === 403 || e.status === 404)) return api('sales/cash-sessions/current/?store=' + encodeURIComponent(ctx.store.id));
        throw e;
      }
    }

    function renderOpenSession() {
      clear(view);
      const cdf = h('input', { type: 'text', inputmode: 'numeric', value: '0' });
      const usd = h('input', { type: 'text', inputmode: 'decimal', value: '0.00' });
      const err = h('div');
      const btn = h('button', { type: 'submit', class: 'btn btn-big' }, 'Ouvrir la caisse');
      const form = h('form', { class: 'card' },
        h('h2', null, 'Ouvrir la caisse'),
        h('p', { class: 'hint' }, 'Comptez l’argent présent dans le tiroir avant de commencer (fonds initial).'), err,
        h('div', { class: 'grid-2' }, field('Fonds en francs (FC)', cdf), field('Fonds en dollars ($)', usd)), btn);
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        clear(err);
        const c = parseAmount(cdf.value, 'CDF');
        const u = parseAmount(usd.value, 'USD');
        if (c === null || u === null) { err.appendChild(errorBox('Montant invalide : francs sans décimales, dollars avec 2 décimales au plus.')); return; }
        await busy(btn, async () => {
          try {
            S.session = await api('sales/cash-sessions/open/', { method: 'POST', body: { store: ctx.store.id, opening_float: { CDF: c, USD: u } } });
            toast('Caisse ouverte.', 'ok');
            startPos();
          } catch (ex) {
            if (ex.status === 409) { toast('La caisse est déjà ouverte.', 'ok'); checkSession(); return; }
            err.appendChild(errorBox(ex.status === 404 && !ex.data ? 'Le module des ventes n’est pas encore disponible sur ce serveur.' : ex.message));
          }
        });
      });
      view.appendChild(form);
    }

    /* ----- 3. Caisse ----- */
    function resetSale() {
      S.currency = (ctx.tenant && ctx.tenant.base_currency) || 'CDF';
      S.cart = [];
      S.customer = null;
      S.mode = 'cash';
      S.discount = '';
      S.cdf = '';
      S.usd = '';
      S.changeCur = S.currency;
      S.clientId = null;
      S.submitting = false;
    }

    async function startPos() {
      resetSale();
      clear(view);
      const rs = () => ctx.rateScaled;

      /* --- éléments --- */
      const root = h('div', { class: 'pos' });
      const search = h('input', { type: 'search', id: 'pos-search', placeholder: 'Rechercher un produit ou scanner un code-barres', autocomplete: 'off', 'aria-label': 'Rechercher un produit, un SKU ou un code-barres', enterkeyhint: 'search' });
      const chips = h('div', { class: 'chips', role: 'group', 'aria-label': 'Catégories' });
      const tiles = h('div', { class: 'tiles' });
      const searchMsg = h('div', { 'aria-live': 'polite' });
      const sessionBar = h('div', { class: 'session-bar' },
        h('span', null, S.session.opened_at ? 'Caisse ouverte à ' + K.fmtDateTime(S.session.opened_at).slice(-5) : 'Caisse ouverte'),
        h('button', { type: 'button', class: 'btn btn-ghost btn-small', onclick: openCloseSession }, 'Clôturer la caisse'));
      const barSum = h('div', { class: 'sum' });
      const barBtn = h('button', { type: 'button', class: 'btn', onclick: () => showCart(true) }, 'Voir le panier ›');
      const cartBar = h('div', { class: 'cart-bar' }, barSum, barBtn);

      const productsPane = h('section', { class: 'pos-products', 'aria-label': 'Produits' },
        sessionBar, h('div', { class: 'pos-searchbar' }, search), chips, searchMsg, tiles);

      /* panier */
      const linesEl = h('div', { 'aria-label': 'Articles du panier' });
      const cartError = h('div');
      const curToggle = h('div', { class: 'mode-toggle', role: 'group', 'aria-label': 'Devise de la vente' });
      const totalsEl = h('div', { class: 'totals' });
      const customerBox = h('div');
      const modeToggle = h('div', { class: 'mode-toggle', role: 'group', 'aria-label': 'Mode de paiement' });
      const discountInput = h('input', { type: 'text', inputmode: 'decimal', placeholder: '0', 'aria-label': 'Remise globale' });
      const cdfInput = h('input', { type: 'text', inputmode: 'numeric', placeholder: '0' });
      const usdInput = h('input', { type: 'text', inputmode: 'decimal', placeholder: '0.00' });
      const paySummary = h('div', { class: 'pay-summary', role: 'status' });
      const changeSel = K.select([['CDF', 'Rendre en francs (FC)'], ['USD', 'Rendre en dollars ($)']], S.changeCur, { 'aria-label': 'Devise de la monnaie rendue' });
      const changeWrap = h('div', { class: 'field' }, changeSel);
      const payBtn = h('button', { type: 'button', class: 'btn btn-big', onclick: submitSale }, 'Encaisser');
      const exactCdf = h('button', { type: 'button', class: 'btn btn-secondary btn-small', onclick: () => fillExact('CDF') }, 'Montant exact en FC');
      const exactUsd = h('button', { type: 'button', class: 'btn btn-secondary btn-small', onclick: () => fillExact('USD') }, 'Montant exact en $');

      const cartPane = h('section', { class: 'pos-cart', 'aria-label': 'Panier et paiement' },
        h('button', { type: 'button', class: 'btn btn-ghost cart-back', onclick: () => showCart(false) }, '‹ Retour aux produits'),
        h('div', { class: 'card' },
          h('h2', null, 'Panier'), curToggle, linesEl,
          h('div', { class: 'grid-2', style: 'margin-top:.7rem' }, field('Remise globale', discountInput, null), h('div'))),
        totalsEl,
        h('div', { class: 'card' }, h('h2', null, 'Client'), customerBox),
        h('div', { class: 'card' },
          h('h2', null, 'Paiement'), modeToggle,
          h('div', { class: 'grid-2' }, field('Reçu en francs (FC)', cdfInput), field('Reçu en dollars ($)', usdInput)),
          h('div', { class: 'btn-row', style: 'margin-bottom:.7rem' }, exactCdf, exactUsd),
          paySummary, changeWrap, cartError, payBtn));

      root.append(productsPane, cartPane, cartBar);
      view.appendChild(root);

      /* --- affichage mobile : produits <-> panier --- */
      function showCart(on) {
        root.classList.toggle('show-cart', on);
        window.scrollTo(0, 0);
      }

      /* --- calculs --- */
      const other = () => (S.currency === 'CDF' ? 'USD' : 'CDF');
      const unitPrice = (p) => convert(p.price_minor, p.currency, S.currency, rs());

      function lineCalc(line) {
        const q = qtyMilli(line.qty);
        if (q === null) return { error: 'Quantité invalide', gross: 0, disc: 0, total: 0 };
        const gross = Number(divRound(BigInt(unitPrice(line.product)) * q, 1000n));
        const disc = parseAmount(line.discount, S.currency);
        if (disc === null) return { error: 'Remise invalide', gross, disc: 0, total: gross };
        if (disc > gross) return { error: 'Remise supérieure au montant', gross, disc: 0, total: gross };
        return { error: null, gross, disc, total: gross - disc };
      }

      function calc() {
        const lines = S.cart.map(lineCalc);
        const subtotal = lines.reduce((a, l) => a + l.total, 0);
        let discount = parseAmount(S.discount, S.currency);
        let discountError = null;
        if (discount === null) { discountError = 'Remise invalide'; discount = 0; }
        if (discount > subtotal) { discountError = 'La remise dépasse le total'; discount = 0; }
        const total = subtotal - discount;
        const cdf = parseAmount(S.cdf, 'CDF');
        const usd = parseAmount(S.usd, 'USD');
        const payError = cdf === null || usd === null;
        const paid = payError ? 0 : paidInCurrency(cdf, usd, S.currency, rs());
        return {
          lines, subtotal, discount, discountError, total, cdf: cdf || 0, usd: usd || 0, payError, paid,
          remaining: Math.max(total - paid, 0), over: Math.max(paid - total, 0),
          lineError: lines.some((l) => l.error),
        };
      }

      /* --- rendu du panier --- */
      function renderCurrencyToggle() {
        clear(curToggle);
        [['CDF', 'Vente en FC'], ['USD', 'Vente en $']].forEach(([c, label]) => {
          curToggle.appendChild(h('button', { type: 'button', 'aria-pressed': S.currency === c ? 'true' : 'false', onclick: () => setCurrency(c) }, label));
        });
      }
      function setCurrency(c) {
        if (c === S.currency) return;
        S.currency = c;
        S.changeCur = c;
        S.discount = '';
        S.cdf = '';
        S.usd = '';
        S.cart.forEach((l) => { l.discount = ''; });
        discountInput.value = '';
        cdfInput.value = '';
        usdInput.value = '';
        changeSel.value = c;
        S.clientId = null;
        renderCurrencyToggle();
        renderLines();
        refresh();
      }

      function renderLines() {
        clear(linesEl);
        if (!S.cart.length) {
          linesEl.appendChild(h('div', { class: 'empty' }, 'Le panier est vide. Touchez un produit pour l’ajouter.'));
          return;
        }
        S.cart.forEach((line, idx) => {
          const p = line.product;
          const totalEl = h('div', { class: 'ln-total' });
          const errEl = h('div', { class: 'error-text', role: 'alert' });
          const warnEl = h('div', { class: 'hint' });
          const qty = h('input', { type: 'text', inputmode: 'decimal', value: line.qty, 'aria-label': 'Quantité : ' + p.name });
          qty.addEventListener('input', () => { line.qty = qty.value; refresh(); });
          const step = (d) => {
            const cur = qtyMilli(line.qty);
            const next = Math.max(1, Math.round(Number(cur || 1000n) / 1000) + d);
            line.qty = String(next);
            qty.value = line.qty;
            refresh();
          };
          const disc = h('input', { type: 'text', inputmode: 'decimal', value: line.discount, placeholder: '0', 'aria-label': 'Remise sur ' + p.name });
          disc.addEventListener('input', () => { line.discount = disc.value; refresh(); });
          line.refs = { totalEl, errEl, warnEl };
          linesEl.appendChild(h('div', { class: 'cart-line' },
            h('div', null, h('div', { class: 'ln-name' }, p.name), h('div', { class: 'hint' }, fmt(unitPrice(p), S.currency) + ' / unité')),
            totalEl,
            h('div', { class: 'ln-ctrl' },
              h('div', { class: 'qty' },
                h('button', { type: 'button', 'aria-label': 'Diminuer la quantité', onclick: () => step(-1) }, '−'), qty,
                h('button', { type: 'button', 'aria-label': 'Augmenter la quantité', onclick: () => step(1) }, '+')),
              h('label', { class: 'ln-disc' }, 'Remise', disc),
              h('button', { type: 'button', class: 'btn btn-danger-outline btn-small', 'aria-label': 'Retirer ' + p.name, onclick: () => { S.cart.splice(idx, 1); renderLines(); refresh(); } }, 'Retirer')),
            h('div', { style: 'grid-column:1/-1' }, errEl, warnEl)));
        });
      }

      function renderModeToggle() {
        clear(modeToggle);
        modeToggle.append(
          h('button', { type: 'button', 'aria-pressed': S.mode === 'cash' ? 'true' : 'false', onclick: () => setMode('cash') }, 'Payer en espèces'),
          h('button', { type: 'button', 'aria-pressed': S.mode === 'credit' ? 'true' : 'false', onclick: () => setMode('credit') }, 'À crédit'));
      }
      function setMode(mode) {
        if (mode === 'credit' && !S.customer) {
          cartError.replaceChildren(errorBox('Choisissez d’abord un client pour vendre à crédit.'));
          customerBox.querySelector('input') && customerBox.querySelector('input').focus();
          return;
        }
        clear(cartError);
        S.mode = mode;
        renderModeToggle();
        refresh();
      }

      /* --- client --- */
      function renderCustomer() {
        clear(customerBox);
        if (S.customer) {
          const c = S.customer;
          const bal = K.fmtMap(c.balance || {});
          const limit = c.credit_limit_minor ? fmt(c.credit_limit_minor, c.credit_limit_currency) : 'aucune';
          customerBox.appendChild(h('div', { class: 'selected-customer' },
            h('div', null, h('strong', null, c.name), h('div', { class: 'hint' }, 'Dette : ' + bal + ' · Limite : ' + limit)),
            h('button', { type: 'button', class: 'btn btn-ghost btn-small', onclick: () => { S.customer = null; S.mode = 'cash'; renderCustomer(); renderModeToggle(); refresh(); } }, 'Changer')));
          return;
        }
        const input = h('input', { type: 'search', placeholder: 'Nom ou téléphone (facultatif)', 'aria-label': 'Rechercher un client', autocomplete: 'off' });
        const results = h('div');
        const doSearch = K.debounce(async () => {
          const q = input.value.trim();
          clear(results);
          if (q.length < 1) return;
          try {
            const data = await api('customers/customers/?q=' + encodeURIComponent(q));
            const list = (data.results || data).filter((c) => c.is_active !== false);
            if (!list.length) { results.appendChild(h('div', { class: 'hint' }, 'Aucun client trouvé.')); return; }
            results.appendChild(h('div', { class: 'customer-results' }, list.slice(0, 8).map((c) => h('button', {
              type: 'button',
              onclick: () => { S.customer = c; renderCustomer(); refresh(); },
            }, h('strong', null, c.name), ' ', c.phone || '', h('div', { class: 'hint' }, 'Dette : ' + K.fmtMap(c.balance || {}))))));
          } catch (e) { results.appendChild(errorBox(e.message)); }
        }, 250);
        input.addEventListener('input', doSearch);
        customerBox.append(input, results,
          K.canWrite.customers(ctx.role) ? h('div', { style: 'margin-top:.5rem' }, h('button', { type: 'button', class: 'btn btn-secondary btn-small', onclick: quickCustomer }, '+ Nouveau client')) : null);
      }

      function quickCustomer() {
        K.openModal('Nouveau client', (body, close) => {
          const name = h('input', { type: 'text', required: true, autocomplete: 'off' });
          const phone = h('input', { type: 'tel', inputmode: 'tel' });
          const limit = h('input', { type: 'text', inputmode: 'numeric', placeholder: '0' });
          const lcur = K.select([['CDF', 'FC'], ['USD', '$']], 'CDF');
          const err = h('div');
          const btn = h('button', { type: 'submit', class: 'btn btn-block' }, 'Enregistrer');
          const form = h('form', null, err, field('Nom', name), field('Téléphone', phone),
            K.canWrite.limit(ctx.role) ? h('div', { class: 'grid-2' }, field('Limite de crédit', limit), field('Devise de la limite', lcur)) : null, btn);
          form.addEventListener('submit', async (e) => {
            e.preventDefault();
            clear(err);
            if (!name.value.trim()) { err.appendChild(errorBox('Le nom est obligatoire.')); return; }
            const body2 = { name: name.value.trim(), phone: K.normalizePhone(phone.value) };
            if (K.canWrite.limit(ctx.role)) {
              const lim = parseAmount(limit.value, lcur.value);
              if (lim === null) { err.appendChild(errorBox('Limite de crédit invalide.')); return; }
              body2.credit_limit_minor = lim;
              body2.credit_limit_currency = lcur.value;
            }
            await busy(btn, async () => {
              try {
                S.customer = await api('customers/customers/', { method: 'POST', body: body2 });
                close();
                renderCustomer();
                refresh();
              } catch (ex) { err.appendChild(errorBox(ex.message)); }
            });
          });
          body.appendChild(form);
        });
      }

      /* --- mise à jour des totaux et du paiement (sans toucher aux champs en cours de saisie) --- */
      function refresh() {
        const c = calc();
        S.cart.forEach((line, i) => {
          const l = c.lines[i];
          if (!line.refs) return;
          line.refs.totalEl.textContent = fmt(l.total, S.currency);
          line.refs.errEl.textContent = l.error || '';
          const stock = S.stock[line.product.id];
          const q = qtyMilli(line.qty);
          line.refs.warnEl.textContent = line.product.track_stock && stock !== undefined && q !== null && Number(q) / 1000 > stock
            ? 'Stock insuffisant : ' + fmtQty(stock) + ' en rayon (la vente reste possible).' : '';
        });
        const count = S.cart.reduce((a, l) => a + (qtyMilli(l.qty) ? Number(qtyMilli(l.qty)) / 1000 : 0), 0);
        const alt = fmt(convert(c.total, S.currency, other(), rs()), other());
        clear(totalsEl).append(
          h('div', { class: 'alt' }, 'Sous-total : ' + fmt(c.subtotal, S.currency) + (c.discount ? ' · Remise : −' + fmt(c.discount, S.currency) : '')),
          h('div', null, 'Total à payer'),
          h('div', { class: 'big' }, fmt(c.total, S.currency)),
          h('div', { class: 'alt' }, '≈ ' + alt + ' (1 $ = ' + fmt(Math.round(parseFloat(ctx.rate)), 'CDF') + ')'));
        if (c.discountError) totalsEl.appendChild(h('div', { class: 'error-text', role: 'alert' }, c.discountError));
        barSum.replaceChildren(S.cart.length ? fmt(c.total, S.currency) : 'Panier vide',
          h('small', null, S.cart.length ? fmtQty(count) + ' article' + (count > 1 ? 's' : '') + ' · ≈ ' + alt : 'Touchez un produit'));
        barBtn.disabled = !S.cart.length;

        // paiement
        let cls = 'pay-summary';
        let text;
        let ok = S.cart.length > 0 && !c.lineError && !c.discountError && !c.payError && c.total > 0;
        const credit = S.mode === 'credit';
        if (c.payError) {
          cls += ' remaining';
          text = 'Montant reçu invalide (FC sans décimales, $ avec 2 décimales au plus).';
        } else if (credit) {
          if (c.over > 0) {
            cls += ' remaining';
            text = 'Le versement dépasse le total : à crédit, on ne rend pas de monnaie.';
            ok = false;
          } else {
            cls += ' credit';
            text = c.remaining > 0 ? 'Mis à crédit : ' + fmt(c.remaining, S.currency) + (c.paid ? ' (acompte ' + fmt(c.paid, S.currency) + ')' : '') : 'Payé en totalité.';
          }
        } else if (c.paid === 0) {
          cls += ' remaining';
          text = 'Saisissez le montant reçu, ou touchez « Montant exact ».';
          ok = false;
        } else if (c.remaining > 0) {
          cls += ' remaining';
          text = 'Reste à payer : ' + fmt(c.remaining, S.currency) + ' (≈ ' + fmt(convert(c.remaining, S.currency, other(), rs()), other()) + ')';
          ok = false;
        } else if (c.over > 0) {
          cls += ' change';
          const chg = convert(c.over, S.currency, S.changeCur, rs());
          text = 'Monnaie à rendre : ' + fmt(chg, S.changeCur);
        } else {
          cls += ' exact';
          text = 'Montant exact reçu.';
        }
        if (K.billing.isReadOnly()) { ok = false; cls = 'pay-summary remaining'; text = roText(); }
        paySummary.className = cls;
        paySummary.textContent = text;
        changeWrap.hidden = !(c.over > 0 && !credit);
        payBtn.disabled = !ok || S.submitting;
        payBtn.textContent = credit ? 'Enregistrer la vente à crédit' : 'Encaisser ' + (c.total ? fmt(c.total, S.currency) : '');
        if (K.billing.isReadOnly()) payBtn.textContent = 'Encaissement indisponible';
        exactCdf.hidden = false;
      }

      function fillExact(cur) {
        const c = calc();
        S.cdf = '';
        S.usd = '';
        const amount = convert(c.total, S.currency, cur, rs());
        if (cur === 'CDF') S.cdf = toInput(amount, 'CDF'); else S.usd = toInput(amount, 'USD');
        cdfInput.value = S.cdf;
        usdInput.value = S.usd;
        refresh();
      }

      discountInput.addEventListener('input', () => { S.discount = discountInput.value; refresh(); });
      cdfInput.addEventListener('input', () => { S.cdf = cdfInput.value; refresh(); });
      usdInput.addEventListener('input', () => { S.usd = usdInput.value; refresh(); });
      changeSel.addEventListener('change', () => { S.changeCur = changeSel.value; refresh(); });

      /* --- ajout au panier --- */
      function addProduct(p) {
        const existing = S.cart.find((l) => l.product.id === p.id);
        if (existing) {
          existing.qty = String(Math.round(Number(qtyMilli(existing.qty) || 0n) / 1000) + 1);
          renderLines();
        } else {
          S.cart.push({ product: p, qty: '1', discount: '' });
          renderLines();
        }
        refresh();
        searchMsg.replaceChildren(h('div', { class: 'alert alert-ok', role: 'status' }, '✔ ' + p.name + ' ajouté'));
        setTimeout(() => { if (searchMsg.firstChild && searchMsg.textContent.includes(p.name)) clear(searchMsg); }, 1500);
      }

      /* --- produits --- */
      function stockLabel(p) {
        if (!p.track_stock) return null;
        const q = S.stock[p.id];
        if (q === undefined) return null;
        const min = parseFloat(p.min_stock || 0);
        if (q <= 0) return h('span', { class: 't-stock out' }, 'Rupture');
        return h('span', { class: 't-stock' + (q <= min ? ' low' : '') }, 'Stock : ' + fmtQty(q));
      }
      function renderTiles() {
        clear(tiles);
        if (!S.products.length) {
          tiles.appendChild(h('div', { class: 'empty more-btn' }, S.query ? 'Aucun produit trouvé.' : 'Aucun produit. Ajoutez-en dans le menu « Produits ».'));
          return;
        }
        S.products.forEach((p) => {
          tiles.appendChild(h('button', { type: 'button', class: 'tile', onclick: () => addProduct(p) },
            h('span', { class: 't-name' }, p.name),
            h('span', null, h('span', { class: 't-price' }, fmt(p.price_minor, p.currency)), h('br'), stockLabel(p))));
        });
        if (S.nextUrl) tiles.appendChild(h('button', { type: 'button', class: 'btn btn-secondary more-btn', onclick: () => loadProducts(false) }, 'Afficher plus de produits'));
      }
      async function loadProducts(reset) {
        const seq = ++S.searchSeq;
        let path;
        if (reset || !S.nextUrl) {
          path = 'catalog/products/?active=1&kind=&q=' + encodeURIComponent(S.query) + (S.category ? '&category=' + S.category : '');
          path = path.replace('&kind=&', '&');
        } else path = S.nextUrl;
        try {
          const data = await api(path);
          if (seq !== S.searchSeq) return null;
          S.products = reset ? data.results : S.products.concat(data.results);
          S.nextUrl = data.next ? data.next.replace(/^https?:\/\/[^/]+\/api\/v1\//, '') : null;
          renderTiles();
          return data.results;
        } catch (e) {
          if (seq === S.searchSeq) { clear(tiles).appendChild(errorBox(e.message)); }
          return null;
        }
      }
      search.addEventListener('input', K.debounce(() => { S.query = search.value.trim(); loadProducts(true); }, 250));
      search.addEventListener('keydown', async (e) => {
        if (e.key !== 'Enter') return;
        e.preventDefault();
        const q = search.value.trim();
        if (!q) return;
        S.query = q;
        const list = await loadProducts(true);
        if (!list) return;
        const exact = list.find((p) => p.barcode === q || p.sku.toLowerCase() === q.toLowerCase());
        const hit = exact || (list.length === 1 ? list[0] : null);
        if (hit) {
          addProduct(hit);
          search.value = '';
          S.query = '';
          loadProducts(true);
        }
      });

      async function loadCategories() {
        try {
          const cats = await api('catalog/categories/');
          const render = () => {
            clear(chips);
            chips.appendChild(h('button', { type: 'button', 'aria-pressed': S.category === '' ? 'true' : 'false', onclick: () => { S.category = ''; render(); loadProducts(true); } }, 'Tous'));
            cats.forEach((c) => chips.appendChild(h('button', { type: 'button', 'aria-pressed': S.category === c.id ? 'true' : 'false', onclick: () => { S.category = c.id; render(); loadProducts(true); } }, c.name)));
          };
          render();
        } catch (e) { /* les catégories sont facultatives */ }
      }
      async function loadStock() {
        try {
          const levels = await K.listAll('stock/levels/?store=' + ctx.store.id);
          S.stock = {};
          levels.forEach((l) => { S.stock[l.product] = parseFloat(l.quantity); });
        } catch (e) { /* le stock est un confort */ }
      }

      /* --- encaissement --- */
      async function submitSale() {
        if (S.submitting) return;
        const c = calc();
        clear(cartError);
        const credit = S.mode === 'credit';
        if (!S.clientId) S.clientId = uuid(); // conservé tant que la vente n'est pas confirmée : réessai sans doublon
        const payments = [];
        if (c.cdf > 0) payments.push({ method: 'cash', amount_minor: c.cdf, currency: 'CDF' });
        if (c.usd > 0) payments.push({ method: 'cash', amount_minor: c.usd, currency: 'USD' });
        const body = {
          client_id: S.clientId,
          store: ctx.store.id,
          currency: S.currency,
          customer: S.customer ? S.customer.id : null,
          items: S.cart.map((l, i) => ({ product: l.product.id, quantity: normQty(l.qty), discount_minor: c.lines[i].disc })),
          discount_minor: c.discount,
          payments,
          credit_remaining: credit,
          change_currency: S.changeCur,
        };
        S.submitting = true;
        payBtn.disabled = true;
        payBtn.textContent = 'Enregistrement…';
        try {
          const sale = await api('sales/', { method: 'POST', body });
          showSuccess(sale);
        } catch (e) {
          S.submitting = false;
          if (e.status === 0 || e.status >= 500) {
            cartError.appendChild(errorBox(e.message + ' La vente n’est peut-être pas enregistrée : touchez de nouveau le bouton, aucune vente en double ne sera créée.'));
          } else if (e.status === 404 && !e.data) {
            S.clientId = null;
            cartError.appendChild(errorBox('Le module des ventes n’est pas encore disponible sur ce serveur.'));
          } else if (e.status === 409) {
            S.clientId = null;
            cartError.appendChild(errorBox(e.message + ' Le panier a changé depuis la dernière tentative : vérifiez la liste des ventes du jour puis réessayez.'));
          } else {
            cartError.appendChild(errorBox(/autorisation/i.test(e.message)
              ? e.message + ' Réduisez la remise (un caissier a un plafond) ou demandez à un gérant d’encaisser.' : e.message));
            if (/taux de change/i.test(e.message)) {
              cartError.appendChild(h('a', { class: 'btn btn-secondary', href: '/app/taux/' }, 'Saisir le taux de change'));
            }
            if (/caisse/i.test(e.message) && /ouvr/i.test(e.message)) {
              cartError.appendChild(h('button', { type: 'button', class: 'btn btn-secondary', onclick: checkSession }, 'Ouvrir la caisse'));
            }
          }
          refresh();
          if (cartError.scrollIntoView) cartError.scrollIntoView({ block: 'center', behavior: 'smooth' });
        }
      }

      /* --- écran de succès --- */
      async function showSuccess(sale) {
        const cur = sale.currency || S.currency;
        const rows = [['Vente', sale.number || ''], ['Total', fmt(sale.total_minor, cur)]];
        if (sale.paid_minor !== undefined) rows.push(['Payé', fmt(sale.paid_minor, cur)]);
        if (sale.change_minor) rows.push(['Monnaie rendue', fmt(sale.change_minor, sale.change_currency || cur)]);
        if (sale.credit_minor) rows.push(['À crédit', fmt(sale.credit_minor, cur)]);
        const frame = h('iframe', { class: 'receipt-frame', title: 'Aperçu du reçu', sandbox: 'allow-same-origin' });
        const frameNote = h('p', { class: 'hint' }, 'Chargement du reçu…');
        clear(view).appendChild(h('div', { class: 'success card' },
          h('div', { class: 'check-mark', 'aria-hidden': 'true' }, '✔'),
          h('h2', null, 'Vente enregistrée'),
          h('dl', null, rows.map(([k, v]) => [h('dt', null, k), h('dd', null, v)])),
          h('div', { class: 'btn-row', style: 'justify-content:center' },
            h('button', { type: 'button', class: 'btn', onclick: () => K.printReceipt(sale.id) }, '🖨 Imprimer le reçu'),
            h('button', { type: 'button', class: 'btn btn-secondary', id: 'new-sale', onclick: () => { loadStock(); startPos(); } }, 'Nouvelle vente')),
          h('div', { style: 'margin-top:1rem' }, frameNote, frame)));
        try {
          frame.srcdoc = await api('sales/' + sale.id + '/receipt/?format=html', { text: true, accept: 'text/html' });
          frameNote.textContent = 'Aperçu du reçu :';
        } catch (e) { frameNote.textContent = 'Aperçu du reçu indisponible : ' + e.message; frame.hidden = true; }
        document.getElementById('new-sale') && document.getElementById('new-sale').focus();
      }

      /* --- clôture de caisse --- */
      function openCloseSession() {
        K.openModal('Clôturer la caisse', (body, close) => {
          const cdf = h('input', { type: 'text', inputmode: 'numeric', placeholder: '0' });
          const usd = h('input', { type: 'text', inputmode: 'decimal', placeholder: '0.00' });
          const err = h('div');
          const out = h('div');
          const btn = h('button', { type: 'submit', class: 'btn btn-block' }, 'Clôturer la caisse');
          const form = h('form', null,
            h('p', { class: 'hint' }, 'Comptez l’argent dans le tiroir, francs et dollars, puis saisissez les montants.'), err,
            h('div', { class: 'grid-2' }, field('Compté en francs (FC)', cdf), field('Compté en dollars ($)', usd)), btn);
          form.addEventListener('submit', async (e) => {
            e.preventDefault();
            clear(err);
            const c = parseAmount(cdf.value, 'CDF');
            const u = parseAmount(usd.value, 'USD');
            if (c === null || u === null) { err.appendChild(errorBox('Montant invalide.')); return; }
            await busy(btn, async () => {
              try {
                const res = await api('sales/cash-sessions/' + S.session.id + '/close/', { method: 'POST', body: { counted: { CDF: c, USD: u } } });
                clear(body);
                body.appendChild(renderCloseResult(res));
                body.appendChild(h('button', { type: 'button', class: 'btn btn-block', onclick: () => { close(); checkSession(); } }, 'Terminer'));
              } catch (ex) { err.appendChild(errorBox(ex.message)); }
            });
          });
          body.append(form, out);
        });
      }

      function renderCloseResult(res) {
        const row = (label, map, signed) => h('tr', null, h('th', { scope: 'row' }, label),
          ['CDF', 'USD'].map((c) => {
            const v = map ? map[c] || 0 : 0;
            const cls = signed ? (v === 0 ? '' : v > 0 ? 'diff-pos' : 'diff-neg') : '';
            return h('td', { class: 'num ' + cls }, (signed && v > 0 ? '+' : '') + fmt(v, c));
          }));
        const diff = res.difference || {};
        const balanced = !(diff.CDF || diff.USD);
        return h('div', null,
          K.alertBox(balanced ? 'Caisse juste : aucun écart.' : 'Écart constaté : vérifiez le tiroir.', balanced ? 'ok' : 'warn'),
          res.sales_count !== undefined ? h('p', null, 'Ventes de la session : ', h('strong', null, String(res.sales_count))) : null,
          h('div', { class: 'table-wrap' }, h('table', null,
            h('thead', null, h('tr', null, h('th'), h('th', { class: 'num' }, 'Francs'), h('th', { class: 'num' }, 'Dollars'))),
            h('tbody', null, row('Attendu', res.expected), row('Compté', res.counted), row('Écart', diff, true)))));
      }

      /* --- initialisation --- */
      renderCurrencyToggle();
      renderLines();
      renderModeToggle();
      renderCustomer();
      refresh();
      loadCategories();
      await Promise.all([loadStock(), loadProducts(true)]);
      renderTiles();
      if (window.matchMedia('(min-width: 900px)').matches) search.focus();
    }
  };
}());
