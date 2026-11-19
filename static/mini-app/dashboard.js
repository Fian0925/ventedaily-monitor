"use strict";

const $ = (id) => document.getElementById(id);
const telegram = window.Telegram?.WebApp;
const demo = !telegram?.initData;
const dateTime = new Intl.DateTimeFormat("id-ID", {
    dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Jakarta",
});
const sizeOrder = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "ALL SIZE", "FREE SIZE"];
let products = [];
let watchlist = new Map();
const selection = new Set();
const selectedVariants = new Map();
const CARD_BATCH = 12;
const filterIds = ["query", "category", "color", "size", "status"];
let filteredGroups = [];
let carouselIndex = 0;
let loaded = false;
let activeView = "stock";
let feedbackTimer;

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
    activeView = view;
    for (const button of document.querySelectorAll("[data-view]")) {
        const id = button.dataset.view;
        $(id).hidden = id !== view;
        if (id === view) button.setAttribute("aria-current", "page");
        else button.removeAttribute("aria-current");
    }
    if (view === "stock") moveCarousel(carouselIndex);
    if (window.scrollY > 160) $(view).scrollIntoView({block: "start"});
}

function stockInfo(value) {
    return {
        aman: ["Tersedia", "available"],
        ready: ["Terbatas", "low"],
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
    row.append(info, node("div", "price", product.price || "—"), actions);
    return row;
}

const swatchColors = [
    [/black|charcoal/, "#2e3336"], [/navy|denim|blue/, "#334d79"], [/cream|bone|white|vanilla|sand/, "#eee3cf"],
    [/mocha|mocca|taupe|wood|brown|choco|latte|coffee|espresso/, "#9d715b"], [/sage|olive|green|mint|moss/, "#7d997e"],
    [/mauve|pink|rose|blush/, "#be8c9f"], [/red|burgundy|maroon/, "#893c4b"], [/purple|lilac|lavender/, "#9c8dad"],
    [/gray|grey|ash|smoke|stone/, "#808b90"], [/peach|orange|salem/, "#d69a7f"], [/mustard|yellow|gold/, "#d8af63"],
];

function variantGroup(label) {
    const field = node("div", "option-group");
    const options = node("div", "options");
    options.setAttribute("role", "group");
    options.setAttribute("aria-label", label);
    field.append(node("span", "option-label", label), options);
    return {field, options};
}

function variantChip(label, selected, change, swatchColor) {
    const button = node("button", "chip");
    button.type = "button";
    button.setAttribute("aria-pressed", String(selected));
    if (swatchColor) {
        const swatch = node("span", "swatch");
        swatch.setAttribute("aria-hidden", "true");
        swatch.style.setProperty("--swatch", swatchColor);
        button.append(swatch);
    }
    button.append(document.createTextNode(label));
    button.addEventListener("click", change);
    return button;
}

function productCard(group) {
    const card = node("article", "product-card");
    const main = node("div", "card-main");
    const title = node("header", "card-title");
    const categories = new Set(group.variants.map(({category}) => category).filter(Boolean));
    const category = categories.size === 1 ? `${[...categories][0]} · ` : "";
    title.append(node("h2", "", group.name), node("p", "variant-count", `${category}${group.variants.length} varian`));

    const colors = variantGroup("Warna");
    const sizes = variantGroup("Ukuran");
    const groupedColors = new Map();
    for (const product of group.variants) {
        const key = filterKey(product.color);
        if (!groupedColors.has(key)) groupedColors.set(key, []);
        groupedColors.get(key).push(product);
    }
    colors.field.hidden = groupedColors.size === 1 && groupedColors.has("");
    main.append(title, colors.field, sizes.field);

    const summary = node("div", "card-summary");
    const details = node("div", "summary-details");
    details.setAttribute("aria-live", "polite");
    const price = node("p", "summary-price");
    const stock = node("div");
    const actions = node("div", "actions");
    details.append(node("span", "summary-label", "Harga varian"), price, stock);
    summary.append(details, actions);

    const previous = group.variants.find(({id}) => id === selectedVariants.get(group.key));
    let selected = previous || group.variants.find(available) || group.variants[0];

    function choose(product, options) {
        selected = product;
        selectedVariants.set(group.key, selected.id);
        showVariant();
        options.querySelector('[aria-pressed="true"]')?.focus({preventScroll: true});
    }

    function showVariant() {
        const currentColor = filterKey(selected.color);
        const colorButtons = [];
        for (const [key, variants] of groupedColors) {
            const color = variants[0].color || "Standar";
            const swatch = swatchColors.find(([pattern]) => pattern.test(key))?.[1];
            colorButtons.push(variantChip(color, key === currentColor, () => {
                const match = variants.find(({size}) => size === selected.size);
                choose(match || variants.find(available) || variants[0], colors.options);
            }, swatch));
        }
        colors.options.replaceChildren(...colorButtons);

        const sizesForColor = new Map(groupedColors.get(currentColor).map((product) => [product.size, product]));
        sizes.options.replaceChildren(...[...sizesForColor.keys()].sort(compareSizes).map((size) => {
            const product = sizesForColor.get(size);
            return variantChip(formatSize(size), size === selected.size, () => choose(product, sizes.options));
        }));

        price.textContent = selected.price || "Belum tersedia";
        stock.replaceChildren(stockPill(selected));
        actions.replaceChildren(...variantActionButtons(selected, "stock"));
    }

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

function groupMatchingProducts() {
    const terms = $("query").value.trim().toLocaleLowerCase("id").split(/\s+/).filter(Boolean);
    const category = $("category").value;
    const color = $("color").value;
    const size = $("size").value;
    const status = $("status").value;
    const groups = new Map();

    for (const product of products) {
        const keywords = `${product.name} ${product.color || ""} ${product.size}`.toLocaleLowerCase("id");
        if ((category && filterKey(product.category) !== category)
                || (color && filterKey(product.color) !== color)
                || (size && product.size !== size)
                || (status && (status === "available" ? !available(product) : product.stock.toLowerCase() !== status))
                || !terms.every((term) => keywords.includes(term))) continue;
        const key = product.name.toLocaleLowerCase("id");
        if (!groups.has(key)) groups.set(key, {key, name: product.name, variants: []});
        groups.get(key).variants.push(product);
    }

    return [...groups.values()].sort((left, right) => left.name.localeCompare(right.name, "id", {numeric: true}));
}

function appendCards(count) {
    const carousel = $("stock-list");
    const start = carousel.children.length;
    const end = Math.min(filteredGroups.length, Math.max(start, count));
    for (const group of filteredGroups.slice(start, end)) carousel.append(productCard(group));
}

function cardOffset(index) {
    const cards = $("stock-list").children;
    return cards[index].offsetLeft - cards[0].offsetLeft;
}

function visibleCardCount() {
    const count = Number(getComputedStyle($("stock-list")).getPropertyValue("--complete-cards")) || 1;
    return Math.min(filteredGroups.length, count);
}

function lastCarouselStart(count = visibleCardCount()) {
    return Math.max(0, filteredGroups.length - count);
}

function showCarouselPosition(count) {
    const first = carouselIndex + 1;
    const last = carouselIndex + count;
    const range = first === last ? first : `${first}–${last}`;
    $("carousel-controls").hidden = filteredGroups.length <= count;
    $("previous").disabled = carouselIndex === 0;
    $("next").disabled = carouselIndex >= lastCarouselStart(count);
    $("carousel-position").textContent = `${range} dari ${filteredGroups.length}`;
    for (const [index, card] of Array.from($("stock-list").children).entries()) {
        card.toggleAttribute("inert", index < carouselIndex || index >= last);
    }
}

function moveCarousel(index) {
    if (!filteredGroups.length) return;
    const count = visibleCardCount();
    carouselIndex = Math.max(0, Math.min(index, lastCarouselStart(count)));
    if (carouselIndex + count + 1 >= $("stock-list").children.length) appendCards(carouselIndex + CARD_BATCH);
    $("stock-list").scrollLeft = cardOffset(carouselIndex);
    showCarouselPosition(count);
}

function onCarouselScroll() {
    const carousel = $("stock-list");
    if (carousel.children.length < 2) return;
    const spacing = cardOffset(1);
    if (spacing <= 0) return;
    const count = visibleCardCount();
    const index = Math.max(0, Math.min(lastCarouselStart(count), Math.round(carousel.scrollLeft / spacing)));
    if (index === carouselIndex) return;
    carouselIndex = index;
    if (index + count + 1 >= carousel.children.length) appendCards(index + CARD_BATCH);
    showCarouselPosition(count);
}

function renderStock() {
    const carousel = $("stock-list");
    filteredGroups = groupMatchingProducts();
    carousel.classList.toggle("is-single", filteredGroups.length < 2);
    $("result-count").textContent = loaded ? `${filteredGroups.length} produk` : "";
    $("clear-filters").hidden = !filterIds.some((id) => $(id).value);
    if (!filteredGroups.length) {
        carouselIndex = 0;
        $("carousel-controls").hidden = true;
        empty("stock-list", loaded ? "Tidak ada produk yang cocok." : "Stok belum dimuat.");
        return;
    }
    carousel.replaceChildren();
    appendCards(Math.min(filteredGroups.length, carouselIndex + CARD_BATCH));
    moveCarousel(carouselIndex);
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

async function load() {
    notice("");
    $("refresh").disabled = true;
    try {
        const data = demo ? {products: examples, watchlist: []} : await api("dashboard");
        setProducts(data.products);
        carouselIndex = 0;
        watchlist = new Map(data.watchlist.map((item) => [item.product_name, item]));
        loaded = true;
        $("catalog-preview").hidden = true;
        const validSelections = new Set(products.filter(available).map(({id}) => id));
        for (const id of selection) {
            if (!validSelections.has(id)) selection.delete(id);
        }
        const update = formatDate(data.updated_at);
        $("stock-time").textContent = demo ? "Cari harga dan ketersediaan per varian." : update ? `Diperbarui ${update}` : "Snapshot stok belum tersedia.";
    } catch (error) {
        $("stock-time").textContent = "Stok belum dapat ditampilkan.";
        const sessionError = error.status === 401 || error.status === 403;
        notice(error.message, sessionError ? "Kembali ke bot" : "Coba lagi", sessionError ? () => telegram.close() : load);
    }
    $("refresh").disabled = false;
    renderLists();
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
        if (result.uses_cost) notes.push("Atur target profit lewat /setmarkup di bot untuk menampilkan harga jual.");
        if (result.missing_price) notes.push("Sebagian varian belum memiliki harga.");
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

for (const button of document.querySelectorAll("[data-view]")) {
    button.addEventListener("click", () => changeView(button.dataset.view));
}
for (const id of filterIds) {
    $(id).addEventListener(id === "query" ? "input" : "change", () => {
        carouselIndex = 0;
        renderStock();
    });
}
$("clear-filters").addEventListener("click", () => {
    for (const id of filterIds) $(id).value = "";
    carouselIndex = 0;
    renderStock();
    $("query").focus();
});
window.addEventListener("resize", () => {
    if (activeView === "stock") moveCarousel(carouselIndex);
});
$("stock-list").addEventListener("scroll", onCarouselScroll, {passive: true});
$("stock-list").addEventListener("keydown", (event) => {
    if (event.target !== $("stock-list") || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    moveCarousel(carouselIndex + (event.key === "ArrowRight" ? 1 : -1));
});
$("previous").addEventListener("click", () => moveCarousel(carouselIndex - 1));
$("next").addEventListener("click", () => moveCarousel(carouselIndex + 1));
$("refresh").addEventListener("click", load);
$("make-catalog").addEventListener("click", makeCatalog);
$("copy").addEventListener("click", copyCatalog);

if (demo) {
    $("mode").hidden = false;
    $("watch-description").textContent = "Pantauan contoh tersimpan selama halaman ini terbuka.";
} else {
    $("refresh").hidden = false;
    telegram.ready();
    telegram.expand();
}
load();
