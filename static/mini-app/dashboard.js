"use strict";

const $ = (id) => document.getElementById(id);
const telegram = window.Telegram?.WebApp;
const demo = !telegram?.initData;
const dateTime = new Intl.DateTimeFormat("id-ID", {
    dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Jakarta",
});
const rupiah = new Intl.NumberFormat("id-ID", {style: "currency", currency: "IDR", maximumFractionDigits: 0});
const wholeNumber = new Intl.NumberFormat("id-ID", {maximumFractionDigits: 0});
const sizeOrder = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "ALL SIZE", "FREE SIZE"];
let products = [];
let watchlist = new Map();
const selection = new Set();
const selectedVariants = new Map();
let optionsAnchor = null;
const PAGE_SIZE = 12;
const filterIds = ["category", "color", "size", "status"];
let filteredGroups = [];
let currentPage = 0;
let scrollTimer;
const resizeActivePage = typeof ResizeObserver === "function"
    ? new ResizeObserver(() => syncCarouselHeight())
    : null;
let loaded = false;
let activeView = "stock";
let feedbackTimer;
let marketplaces;
let loadingPricing = false;
let stockLoad = 0;
let pricingIsCurrent = false;

const examples = [
    ["Sasmita", "Family", "Navy", "L", "Aman", "Rp 149.000"],
    ["Sasmita", "Family", "Navy", "XL", "Habis", "Rp 149.000"],
    ["Sasmita", "Family", "Black", "M", "Ready", "Rp 149.000"],
    ["Aruna", "Couple", "Sage", "M", "Ready", "Rp 164.000"],
    ["Aruna", "Couple", "Sage", "L", "Aman", "Rp 164.000"],
    ["Aruna", "Couple", "Cream", "L", "Habis", "Rp 169.000"],
    ["Kirana", "Tunik Motif", "Mauve", "M", "Aman", "Rp 159.000"],
    ["Kirana", "Tunik Motif", "Mauve", "L", "Habis", "Rp 159.000"],
    ["Nala", "Gamis Motif", "Sand", "ALL SIZE", "Ready", "Rp 99.000"],
    ["Alea", "Oneset Motif", "Mocha", "M", "Aman", "Rp 119.000"],
    ["Alea", "Oneset Motif", "Mocha", "L", "Aman", "Rp 119.000"],
    ["Alea", "Oneset Motif", "Mocha", "XL", "Habis", "Rp 119.000"],
    ["Alea", "Oneset Motif", "Sage", "M", "Ready", "Rp 119.000"],
    ["Alea", "Oneset Motif", "Sage", "L", "Habis", "Rp 119.000"],
    ["Alea", "Oneset Motif", "Cream", "M", "Aman", "Rp 125.000"],
    ["Box Vente", "Box", "Mint", "ALL SIZE", "Aman", "Rp 35.000"],
    ["Dara", "Tunik Brukat", "Lavender", "M", "Aman", "Rp 135.000"],
    ["Elina", "Gamis Motif", "Cream", "L", "Ready", "Rp 189.000"],
    ["Farah", "Oneset Rok", "Navy", "L", "Aman", "Rp 165.000"],
    ["Gisella", "Tunik Motif", "Navy", "XL", "Habis", "Rp 139.000"],
    ["Hana", "Tunik Polos", "Sage", "M", "Aman", "Rp 115.000"],
    ["Jelita", "Gamis Motif", "Dusty Pink", "L", "Ready", "Rp 178.000"],
    ["Laras", "Oneset Motif", "Black", "M", "Aman", "Rp 159.000"],
    ["Maira", "Couple", "Mocha", "L", "Ready", "Rp 179.000"],
    ["Rania", "Tunik Brukat", "Cream", "XL", "Aman", "Rp 148.000"],
    ["Safira", "Gamis Motif", "Navy", "L", "Habis", "Rp 199.000"],
    ["Zahra", "Tunik Motif", "Mauve", "M", "Aman", "Rp 132.000"],
].map(([name, category, color, size, stock, price]) => ({id: `${name} ${color} SIZE ${size}`, name, category, color, size, stock, price}));

function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
}

function action(label, callback, selected = false) {
    const button = node("button", `button secondary${selected ? " selected" : ""}`, label);
    button.type = "button";
    button.addEventListener("click", () => callback(button));
    return button;
}

function formatDate(value) {
    const date = new Date(value);
    return value && !Number.isNaN(date.getTime()) ? `${dateTime.format(date)} WIB` : "";
}

function formatSize(size) {
    return {"ALL SIZE": "All Size", "FREE SIZE": "Free Size"}[size] || size;
}

function notice(message, label = "", callback = null) {
    $("notice").hidden = !message;
    $("notice-text").textContent = message;
    $("notice-action").hidden = !label;
    $("notice-action").textContent = label;
    $("notice-action").onclick = callback;
}

function feedback(message) {
    clearTimeout(feedbackTimer);
    $("feedback").textContent = message;
    $("feedback").hidden = false;
    feedbackTimer = setTimeout(() => { $("feedback").hidden = true; }, 3600);
}

function changeView(view) {
    closeProductOptions();
    activeView = view;
    for (const button of document.querySelectorAll("[data-view]")) {
        const id = button.dataset.view;
        $(id).hidden = id !== view;
        if (id === view) button.setAttribute("aria-current", "page");
        else button.removeAttribute("aria-current");
    }
    if (view === "stock") renderPage(currentPage);
    if (view === "pricing") loadPricing();
    if (window.scrollY > 160) $(view).scrollIntoView({block: "start"});
}

function stockInfo(value) {
    return {
        aman: ["Aman", "available"],
        ready: ["Ready", "low"],
        habis: ["Habis", "out"],
    }[value.toLowerCase()] || ["Tidak terdata", ""];
}

function available(product) {
    return ["aman", "ready"].includes(product.stock.toLowerCase());
}

async function api(path, method = "GET", body) {
    const headers = {Authorization: `tma ${telegram.initData}`};
    if (body) headers["Content-Type"] = "application/json";
    const response = await fetch(`api/${path}`, {
        method, headers, body: body ? JSON.stringify(body) : undefined,
    });
    if (response.status === 204) return;
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        const error = new Error(data.error || "Koneksi bermasalah. Coba lagi.");
        error.status = response.status;
        throw error;
    }
    return data;
}

function handleError(error) {
    if (error.status === 401 || error.status === 403) {
        notice(error.message, "Kembali ke bot", () => telegram.close());
    } else {
        feedback(error.message);
    }
}

function variantActionButtons(product, context) {
    const buttons = [];
    if (context !== "catalog") {
        const watched = watchlist.has(product.id);
        const label = context === "watch" ? "Hapus pantauan" : watched ? "Dipantau" : "Pantau";
        const toggle = action(label, (button) => toggleWatch(product, button, !watched), watched && context === "stock");
        if (context === "stock") toggle.setAttribute("aria-pressed", String(watched));
        buttons.push(toggle);
    }
    if (context === "catalog" || (context === "stock" && available(product))) {
        const selected = selection.has(product.id);
        const label = context === "catalog" ? "Hapus" : selected ? "Dipilih" : "Ke katalog";
        const toggle = action(label, () => toggleProduct(product), selected && context === "stock");
        if (context === "stock") {
            toggle.setAttribute("aria-pressed", String(selected));
            if (!selected) toggle.classList.replace("secondary", "primary");
        }
        buttons.push(toggle);
    }
    return buttons;
}

function stockPill(product) {
    const [label, style] = stockInfo(product.stock);
    return node("span", `pill ${style}`, label);
}

function showProductPrice(element, product) {
    const origin = product.price_source;
    const hasEstimate = pricingIsCurrent && Number.isFinite(product.selling_price);
    let label = "Harga belum tersedia";
    let detail = "";
    if (demo) label = "Harga contoh";
    else if (hasEstimate) {
        label = "Harga jual";
        detail = `Modal Vente ${product.price}`;
    } else if (origin === "reseller") label = "Modal Vente";
    else if (origin === "catalog") label = "Katalog Vente";

    const price = hasEstimate ? rupiah.format(product.selling_price) : (origin || demo) ? product.price : "—";
    element.replaceChildren(node("span", "summary-label", label), node("span", "summary-price", price));
    if (detail) element.append(node("span", "price-origin", detail));
}

function productRow(product, context, lastRestock = null) {
    const row = node("article", "product");
    const info = node("div");
    info.append(node("h3", "", product.name));
    const meta = node("div", "meta");
    const variant = [product.color, formatSize(product.size)].filter(Boolean).join(" · ");
    meta.append(node("span", "", variant), stockPill(product));
    if (context === "watch") {
        const date = formatDate(lastRestock);
        meta.append(node("span", "", date ? `Restock ${date}` : "Belum ada riwayat restock"));
    }
    info.append(meta);
    const actions = node("div", "actions");
    actions.append(...variantActionButtons(product, context));
    const price = node("div", "price-display");
    showProductPrice(price, product);
    row.append(info, price, actions);
    return row;
}

function closeProductOptions(restoreFocus = false) {
    if (!optionsAnchor) return;
    const anchor = optionsAnchor;
    optionsAnchor = null;
    anchor.setAttribute("aria-expanded", "false");
    $("product-options").hidden = true;
    if (restoreFocus) anchor.focus({preventScroll: true});
}

function toggleProductOptions(anchor, product) {
    if (optionsAnchor === anchor) {
        closeProductOptions();
        return;
    }
    closeProductOptions();

    const menu = $("product-options");
    menu.replaceChildren(...variantActionButtons(product, "stock"));
    if (product.price_source === "reseller") {
        menu.append(action("Hitung harga jual", () => openProductCalculator(product)));
    }
    optionsAnchor = anchor;
    anchor.setAttribute("aria-expanded", "true");
    menu.hidden = false;
    const rect = anchor.getBoundingClientRect();
    const margin = 8;
    const maxLeft = window.innerWidth - menu.offsetWidth - margin;
    const left = Math.max(margin, Math.min(maxLeft, rect.right - menu.offsetWidth));
    const fitsBelow = rect.bottom + menu.offsetHeight + margin < window.innerHeight;
    const top = fitsBelow ? rect.bottom + 4 : Math.max(margin, rect.top - menu.offsetHeight - 4);
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
    menu.firstElementChild?.focus({preventScroll: true});
}

function variantMatrix(variants, choose) {
    const matrix = node("div", "variant-fields");
    matrix.setAttribute("role", "group");
    matrix.setAttribute("aria-label", "Warna dan ukuran");
    const buttons = new Map();
    const colors = new Map();

    for (const variant of variants) {
        const key = filterKey(variant.color);
        if (!colors.has(key)) colors.set(key, []);
        colors.get(key).push(variant);
    }

    for (const group of colors.values()) {
        const line = node("div", "variant-line");
        const color = group[0].color || "Standar";
        const sizes = node("div", "size-list");
        for (const variant of group.sort((left, right) => compareSizes(left.size, right.size))) {
            const size = formatSize(variant.size);
            const [label, tone] = stockInfo(variant.stock);
            const button = node("button", `size-chip ${tone}`, size);
            button.type = "button";
            button.title = `${color} · ${size} · ${label}`;
            button.setAttribute("aria-label", button.title);
            button.addEventListener("click", () => choose(variant));
            sizes.append(button);
            buttons.set(variant.id, button);
        }
        line.append(node("span", "variant-color", color), sizes);
        matrix.append(line);
    }
    return {matrix, buttons};
}

function productCard(group) {
    const card = node("article", "product-card");
    const main = node("div", "card-main");
    const title = node("header", "card-title");
    const name = node("h2", "", group.name);
    name.title = group.name;
    const categories = new Set(group.variants.map(({category}) => category).filter(Boolean));
    const category = categories.size === 1 ? `${[...categories][0]} · ` : "";
    const meta = node("p", "variant-count", `${category}${group.variants.length} varian`);
    meta.title = meta.textContent;
    title.append(name, meta);

    const summary = node("div", "card-summary");
    const details = node("div", "summary-details");
    const price = node("div", "price-display");
    price.setAttribute("aria-live", "polite");
    const options = node("button", "options-toggle", "Opsi");
    options.type = "button";
    options.setAttribute("aria-label", `Opsi untuk ${group.name}`);
    options.setAttribute("aria-controls", "product-options");
    options.setAttribute("aria-expanded", "false");
    details.append(price, options);
    summary.append(details);

    const previous = group.variants.find(({id}) => id === selectedVariants.get(group.key));
    let selected = previous
        || group.variants.find((product) => available(product) && Number.isFinite(product.selling_price))
        || group.variants.find(available)
        || group.variants[0];
    const {matrix, buttons} = variantMatrix(group.variants, choose);
    main.append(title, matrix);

    function showVariant() {
        for (const [id, button] of buttons) {
            button.setAttribute("aria-pressed", String(id === selected.id));
        }
        showProductPrice(price, selected);
    }

    function choose(product) {
        selected = product;
        selectedVariants.set(group.key, selected.id);
        showVariant();
    }

    options.addEventListener("click", () => toggleProductOptions(options, selected));

    showVariant();
    card.append(main, summary);
    return card;
}

function empty(list, message) {
    $(list).replaceChildren(node("p", "empty", message));
}

function filterKey(value) {
    return value ? value.toLocaleLowerCase("id") : "";
}

function searchText(value) {
    return filterKey(value).normalize("NFD").replace(/[\u0300-\u036f]/g, "")
        .replace(/[^\p{L}\p{N}]+/gu, " ").trim();
}

const exactSizeTerms = new Set(sizeOrder.map(searchText).filter((size) => !size.includes(" ")));

function matchesSearchTerm(text, term) {
    if (term.length < 3) return text.split(" ").some((word) => word.startsWith(term));
    return text.includes(term);
}

function searchRank(product, query, terms) {
    if (!query) return 0;
    const name = searchText(product.name);
    if (name === query) return 3;

    const color = searchText(product.color);
    const size = searchText(product.size);
    const category = searchText(product.category);
    const fields = `${name} ${color} ${size} ${category}`;
    for (const term of terms) {
        // With a product query, a size token must match the actual variant size.
        if (terms.length > 1 && exactSizeTerms.has(term)) {
            if (!size.split(" ").includes(term)) return -1;
        } else if (!matchesSearchTerm(fields, term)) {
            return -1;
        }
    }

    if (name.startsWith(query)) return 2;
    if (terms.some((term) => matchesSearchTerm(name, term))) return 1;
    return 0;
}

function groupMatchingProducts(query) {
    const terms = query ? query.split(" ") : [];
    const category = $("category").value;
    const color = $("color").value;
    const size = $("size").value;
    const status = $("status").value;
    const groups = new Map();

    for (const product of products) {
        if ((category && filterKey(product.category) !== category)
                || (color && filterKey(product.color) !== color)
                || (size && product.size !== size)
                || (status && (status === "available" ? !available(product) : product.stock.toLowerCase() !== status))) continue;

        const rank = searchRank(product, query, terms);
        if (rank < 0) continue;
        const key = filterKey(product.name);
        if (!groups.has(key)) groups.set(key, {key, name: product.name, variants: [], rank});
        const group = groups.get(key);
        group.variants.push(product);
        group.rank = Math.max(group.rank, rank);
    }

    return [...groups.values()].sort((left, right) => right.rank - left.rank
        || left.name.localeCompare(right.name, "id", {numeric: true}));
}

function pageCount() {
    return Math.ceil(filteredGroups.length / PAGE_SIZE);
}

function pageOffset(page) {
    return page.offsetLeft - $("stock-list").firstElementChild.offsetLeft;
}

function syncCarouselHeight(page = $("stock-list").querySelector(".is-current")) {
    if (page?.offsetHeight) $("stock-list").style.height = `${page.offsetHeight}px`;
}

function renderPage(index) {
    closeProductOptions();
    clearTimeout(scrollTimer);
    const total = pageCount();
    if (!total) return;

    currentPage = Math.max(0, Math.min(index, total - 1));
    const carousel = $("stock-list");
    const pages = [];
    for (let number = Math.max(0, currentPage - 1); number <= Math.min(total - 1, currentPage + 1); number++) {
        const page = node("div", "product-page");
        const active = number === currentPage;
        page.dataset.page = number;
        page.classList.toggle("is-current", active);
        page.toggleAttribute("inert", !active);
        const start = number * PAGE_SIZE;
        page.append(...filteredGroups.slice(start, start + PAGE_SIZE).map(productCard));
        pages.push(page);
    }

    resizeActivePage?.disconnect();
    carousel.replaceChildren(...pages);
    const activePage = carousel.querySelector(".is-current");
    carousel.scrollLeft = pageOffset(activePage);
    syncCarouselHeight(activePage);
    resizeActivePage?.observe(activePage);
    $("carousel-controls").hidden = total <= 1;
    $("previous").disabled = currentPage === 0;
    $("next").disabled = currentPage >= total - 1;
    $("carousel-position").textContent = `${currentPage + 1} / ${total}`;
    $("carousel-position").setAttribute("aria-label", `Halaman ${currentPage + 1} dari ${total}`);
}

function onCarouselScroll() {
    closeProductOptions();
    clearTimeout(scrollTimer);
    scrollTimer = setTimeout(() => {
        const carousel = $("stock-list");
        if (carousel.children.length < 2 || !carousel.clientWidth) return;
        const closest = Array.from(carousel.children).reduce((nearest, page) => (
            Math.abs(pageOffset(page) - carousel.scrollLeft) < Math.abs(pageOffset(nearest) - carousel.scrollLeft) ? page : nearest
        ));
        if (Number(closest.dataset.page) !== currentPage) renderPage(Number(closest.dataset.page));
    }, 100);
}

function renderStock() {
    closeProductOptions();
    const search = $("query").value.trim();
    const query = searchText(search);
    const carousel = $("stock-list");
    const filters = $("stock-filters");
    carousel.classList.toggle("is-search", Boolean(query));
    filters.classList.toggle("is-search", Boolean(query));
    $("stock").classList.toggle("has-query", Boolean(query));
    if (!query) setFiltersOpen(false);
    filteredGroups = groupMatchingProducts(query);

    const count = filteredGroups.length;
    $("stock-legend").hidden = !count;
    $("result-count").textContent = loaded ? `${count} produk${query ? " ditemukan" : ""}` : "";
    const activeFilters = filterIds.filter((id) => $(id).value).length;
    $("toggle-filters").textContent = activeFilters ? `Filter (${activeFilters})` : "Filter";
    $("clear-filters").hidden = activeFilters === 0;
    if (!filteredGroups.length) {
        currentPage = 0;
        clearTimeout(scrollTimer);
        resizeActivePage?.disconnect();
        carousel.style.height = "";
        $("carousel-controls").hidden = true;
        const message = query ? `Tidak ada produk untuk “${search}”.` : "Tidak ada produk yang cocok dengan filter.";
        empty("stock-list", loaded ? message : "Stok belum dimuat.");
        return;
    }
    renderPage(currentPage);
}

function renderLists() {
    renderStock();
    if (watchlist.size) {
        const rows = Array.from(watchlist.values(), (row) => productRow(row.product, "watch", row.last_restock));
        $("watch-list").replaceChildren(...rows);
    } else {
        empty("watch-list", "Belum ada pantauan. Pilih Pantau pada varian di Cari Stok.");
    }
    const selected = products.filter((product) => selection.has(product.id));
    if (selected.length) {
        $("catalog-list").replaceChildren(...selected.map((product) => productRow(product, "catalog")));
    } else {
        empty("catalog-list", "Belum ada produk. Pilih Ke katalog pada varian yang tersedia.");
    }
    $("make-catalog").disabled = !selected.length;
    for (const [id, count] of [["watch-count", watchlist.size], ["catalog-count", selection.size]]) {
        $(id).textContent = count;
        $(id).hidden = !count;
    }
}

function toggleProduct(product) {
    if (selection.has(product.id)) {
        selection.delete(product.id);
        feedback("Varian dihapus dari katalog.");
    } else {
        if (selection.size >= 50) {
            feedback("Katalog dapat memuat hingga 50 varian.");
            return;
        }
        selection.add(product.id);
        feedback("Varian ditambahkan ke katalog.");
    }
    $("catalog-preview").hidden = true;
    renderLists();
}

async function toggleWatch(product, button, enabled) {
    button.disabled = true;
    try {
        const result = demo ? null : await api("watchlist", enabled ? "POST" : "DELETE", {product: product.id});
        if (enabled) watchlist.set(product.id, {product, last_restock: result?.last_restock});
        else watchlist.delete(product.id);
        renderLists();
        feedback(enabled ? "Varian mulai dipantau." : "Pantauan dihapus.");
    } catch (error) {
        handleError(error);
        button.disabled = false;
    }
}

function setFilterOptions(id, placeholder, values, label) {
    const select = $(id);
    const previous = select.value;
    select.replaceChildren(new Option(placeholder, ""), ...values.map((value) => new Option(label(value), value)));
    select.value = values.includes(previous) ? previous : "";
}

function compareSizes(left, right) {
    const rank = (size) => sizeOrder.includes(size) ? sizeOrder.indexOf(size) : sizeOrder.length;
    return rank(left) - rank(right) || left.localeCompare(right, "id", {numeric: true});
}

function setProducts(items) {
    products = items;
    const sizes = Array.from(new Set(products.map(({size}) => size))).sort(compareSizes);
    setFilterOptions("size", "Semua ukuran", sizes, formatSize);
    for (const [id, field, placeholder] of [
        ["category", "category", "Semua jenis"],
        ["color", "color", "Semua warna"],
    ]) {
        const labels = new Map(products.filter((product) => product[field])
            .map((product) => [filterKey(product[field]), product[field]]));
        const ordered = [...labels.keys()].sort((left, right) => left.localeCompare(right, "id"));
        setFilterOptions(id, placeholder, ordered, (key) => labels.get(key));
    }
}

function showPriceGuide(pricing) {
    $("show-price-guide").hidden = demo || !loaded;
    let message = "Periksa setelan toko di Pengaturan untuk menampilkan harga jual otomatis.";
    if (pricing) {
        const target = pricing.markup_type === "nominal"
            ? rupiah.format(Number(pricing.markup_value)) : `${pricing.markup_value.replace(".", ",")}%`;
        const fee = pricing.admin_fee.replace(".", ",");
        message = `${pricing.marketplace} · Admin ${fee}% · Target untung ${target} · Packing ${rupiah.format(pricing.packing_cost)}`;
    }
    $("price-settings-line").textContent = message;
}

async function load({resetPage = true} = {}) {
    const request = ++stockLoad;
    notice("");
    $("refresh").disabled = true;
    try {
        const data = demo ? {products: examples, watchlist: []} : await api("dashboard");
        if (request !== stockLoad) return false;
        setProducts(data.products);
        if (resetPage) currentPage = 0;
        watchlist = new Map(data.watchlist.map((item) => [item.product_name, item]));
        loaded = true;
        pricingIsCurrent = true;
        showPriceGuide(data.pricing);
        $("catalog-preview").hidden = true;
        const validSelections = new Set(products.filter(available).map(({id}) => id));
        for (const id of selection) {
            if (!validSelections.has(id)) selection.delete(id);
        }
        const update = formatDate(data.updated_at);
        $("stock-time").textContent = demo ? "Cari harga dan ketersediaan per varian." : update ? `Diperbarui ${update}` : "Snapshot stok belum tersedia.";
    } catch (error) {
        if (request !== stockLoad) return false;
        $("stock-time").textContent = "Stok belum dapat ditampilkan.";
        const sessionError = error.status === 401 || error.status === 403;
        notice(error.message, sessionError ? "Kembali ke bot" : "Coba lagi", sessionError ? () => telegram.close() : load);
        return false;
    } finally {
        if (request === stockLoad) {
            $("refresh").disabled = false;
            renderLists();
        }
    }
    return true;
}

async function makeCatalog() {
    $("make-catalog").disabled = true;
    try {
        let result;
        if (demo) {
            const lines = products.filter(({id}) => selection.has(id))
                .map((item) => `• ${item.name} ${item.color} (${formatSize(item.size)}) — ${item.price}`);
            result = {text: ["CONTOH KATALOG", "", ...lines].join("\n")};
        } else {
            result = await api("catalog", "POST", {products: [...selection]});
        }
        $("catalog-text").value = result.text;
        const notes = [];
        if (result.zero_profit) notes.push("Target untung masih nol. Ubah di Pengaturan jika perlu.");
        if (result.missing_price) notes.push("Varian tanpa estimasi harga jual ditulis sebagai “Tanya harga”.");
        $("catalog-note").textContent = notes.join(" ");
        $("catalog-note").hidden = notes.length === 0;
        $("catalog-preview").hidden = false;
        if (activeView === "catalog") $("catalog-preview").scrollIntoView({block: "nearest"});
    } catch (error) {
        handleError(error);
    } finally {
        $("make-catalog").disabled = !selection.size;
    }
}

async function copyCatalog() {
    const text = $("catalog-text");
    try {
        await navigator.clipboard.writeText(text.value);
        feedback("Teks katalog disalin.");
    } catch {
        text.focus();
        text.select();
        feedback("Teks dipilih. Salin dari menu perangkatmu.");
    }
}

function pricingStatus(message, error = false) {
    const status = $("pricing-status");
    status.textContent = message;
    status.hidden = !message;
    status.classList.toggle("is-error", error);
}

function setMarkupUnit() {
    const nominal = $("pricing-kind").value === "nominal";
    $("pricing-unit").textContent = nominal ? "Rp" : "%";
    $("pricing-markup").inputMode = nominal ? "numeric" : "decimal";
}

function setPricingValues(settings) {
    $("pricing-marketplace").value = settings.marketplace;
    $("pricing-fee").value = settings.admin_fee.replace(".", ",");
    $("pricing-kind").value = settings.markup_type;
    $("pricing-markup").value = settings.markup_type === "nominal" && /^[0-9]+$/.test(settings.markup_value)
        ? wholeNumber.format(Number(settings.markup_value)) : settings.markup_value.replace(".", ",");
    setMarkupUnit();
}

async function loadPricing() {
    if (demo) {
        $("pricing-load-status").textContent = "Buka Mini App dari bot Telegram untuk memakai kalkulator dan menyimpan setelan.";
        return;
    }
    if (marketplaces || loadingPricing) return;
    loadingPricing = true;
    $("pricing-load-status").textContent = "Memuat pengaturan…";
    $("pricing-retry").hidden = true;
    try {
        const data = await api("pricing/settings");
        marketplaces = data.marketplaces;
        $("pricing-marketplace").replaceChildren(...Object.entries(marketplaces)
            .map(([key, {name}]) => new Option(name, key)));
        $("pricing-packing").textContent = rupiah.format(data.packing_cost);
        setPricingValues(data.settings);
        $("pricing-load").hidden = true;
        $("pricing-content").hidden = false;
    } catch (error) {
        $("pricing-load-status").textContent = error.message;
        $("pricing-retry").hidden = error.status === 401 || error.status === 403;
    } finally {
        loadingPricing = false;
    }
}

function openProductCalculator(product) {
    $("pricing-cost").value = product.price.replace(/^Rp\.?\s*/i, "");
    $("pricing-product").textContent = [product.name, product.color, formatSize(product.size)].filter(Boolean).join(" · ");
    $("pricing-product").hidden = false;
    $("pricing-result").hidden = true;
    pricingStatus("");
    changeView("pricing");
}

async function submitPricing(save) {
    const fields = $("pricing-fields");
    if (fields.disabled || !marketplaces) return;
    const values = {
        marketplace: $("pricing-marketplace").value,
        admin_fee: $("pricing-fee").value,
        markup_type: $("pricing-kind").value,
        markup_value: $("pricing-markup").value,
    };
    const cost = $("pricing-cost").value;
    fields.disabled = true;
    pricingStatus(save ? "Menyimpan setelan…" : "Menghitung harga jual…");
    try {
        if (save) {
            const {settings} = await api("pricing/settings", "PUT", values);
            setPricingValues(settings);
            $("catalog-preview").hidden = true;
            pricingIsCurrent = false;
            showPriceGuide(null);
            renderLists();
            pricingStatus("Setelan tersimpan. Memperbarui harga produk…");
            const refreshed = await load({resetPage: false});
            pricingStatus(refreshed ? "Setelan tersimpan. Harga produk sudah diperbarui."
                : "Setelan tersimpan. Muat ulang stok untuk melihat harga baru.");
        } else {
            const result = await api("pricing/calculate", "POST", {...values, cost});
            if (activeView !== "pricing" || cost !== $("pricing-cost").value) {
                pricingStatus("");
                return;
            }
            $("result-selling-price").textContent = rupiah.format(result.harga_jual);
            $("result-cost").textContent = rupiah.format(result.modal);
            $("result-fee").textContent = rupiah.format(result.admin_fee);
            $("result-profit").textContent = rupiah.format(result.actual_profit);
            $("pricing-result").hidden = false;
            $("pricing-result").scrollIntoView({block: "nearest"});
            pricingStatus("");
        }
    } catch (error) {
        pricingStatus(error.message, true);
    } finally {
        fields.disabled = false;
    }
}

for (const button of document.querySelectorAll("[data-view]")) {
    button.addEventListener("click", () => changeView(button.dataset.view));
}
function setFiltersOpen(open) {
    $("stock-filters").classList.toggle("filters-open", open);
    $("toggle-filters").setAttribute("aria-expanded", String(open));
}

$("toggle-filters").addEventListener("click", () => {
    setFiltersOpen($("toggle-filters").getAttribute("aria-expanded") !== "true");
});

for (const id of ["query", ...filterIds]) {
    $(id).addEventListener(id === "query" ? "input" : "change", () => {
        currentPage = 0;
        renderStock();
    });
}
$("clear-filters").addEventListener("click", () => {
    for (const id of filterIds) $(id).value = "";
    currentPage = 0;
    renderStock();
    $("query").focus();
});
document.addEventListener("click", (event) => {
    if (optionsAnchor && !optionsAnchor.contains(event.target) && !$("product-options").contains(event.target)) {
        closeProductOptions();
    }
});
document.addEventListener("focusin", (event) => {
    if (optionsAnchor && !optionsAnchor.contains(event.target) && !$("product-options").contains(event.target)) {
        closeProductOptions();
    }
});
document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && optionsAnchor) {
        event.preventDefault();
        closeProductOptions(true);
    }
});
window.addEventListener("scroll", () => closeProductOptions(), {passive: true});
window.addEventListener("resize", () => {
    closeProductOptions();
    if (activeView === "stock") {
        const activePage = $("stock-list").querySelector(".is-current");
        if (activePage) $("stock-list").scrollLeft = pageOffset(activePage);
        syncCarouselHeight(activePage);
    }
});
$("stock-list").addEventListener("scroll", onCarouselScroll, {passive: true});
$("stock-list").addEventListener("keydown", (event) => {
    if (event.target !== $("stock-list") || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    renderPage(currentPage + (event.key === "ArrowRight" ? 1 : -1));
});
$("previous").addEventListener("click", () => renderPage(currentPage - 1));
$("next").addEventListener("click", () => renderPage(currentPage + 1));
$("refresh").addEventListener("click", load);
$("show-price-guide").addEventListener("click", (event) => {
    const guide = $("price-guide");
    guide.hidden = !guide.hidden;
    event.currentTarget.setAttribute("aria-expanded", String(!guide.hidden));
});
$("make-catalog").addEventListener("click", makeCatalog);
$("copy").addEventListener("click", copyCatalog);
$("pricing-retry").addEventListener("click", loadPricing);
$("pricing-marketplace").addEventListener("change", (event) => {
    $("pricing-fee").value = String(marketplaces[event.target.value].fee).replace(".", ",");
});
$("pricing-kind").addEventListener("change", () => {
    $("pricing-markup").value = "";
    setMarkupUnit();
});
$("pricing-cost").addEventListener("input", () => { $("pricing-product").hidden = true; });
$("pricing-form").addEventListener("input", () => {
    $("pricing-result").hidden = true;
    pricingStatus("");
});
$("pricing-form").addEventListener("submit", (event) => {
    event.preventDefault();
    submitPricing(false);
});
$("save-pricing").addEventListener("click", () => submitPricing(true));

if (demo) {
    $("mode").hidden = false;
    $("watch-description").textContent = "Pantauan contoh tersimpan selama halaman ini terbuka.";
} else {
    $("refresh").hidden = false;
    telegram.ready();
    telegram.expand();
}
load();
