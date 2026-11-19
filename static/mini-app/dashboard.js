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
let filteredGroups = [];
let carouselIndex = 0;
let loaded = false;
let activeView = "stock";
let feedbackTimer;

const examples = [
    ["Sasmita", "Navy", "L", "Aman", "Rp 149.000"],
    ["Sasmita", "Navy", "XL", "Habis", "Rp 149.000"],
    ["Sasmita", "Black", "M", "Ready", "Rp 149.000"],
    ["Aruna", "Sage", "M", "Ready", "Rp 164.000"],
    ["Aruna", "Sage", "L", "Aman", "Rp 164.000"],
    ["Aruna", "Cream", "L", "Habis", "Rp 169.000"],
    ["Kirana", "Mauve", "M", "Aman", "Rp 159.000"],
    ["Kirana", "Mauve", "L", "Habis", "Rp 159.000"],
    ["Nala", "Sand", "ALL SIZE", "Ready", "Rp 99.000"],
    ["Alea", "Mocha", "ALL SIZE", "Aman", "Rp 119.000"],
].map(([name, color, size, stock, price]) => ({id: `${name} ${color} SIZE ${size}`, name, color, size, stock, price}));

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
        if (context === "stock") toggle.setAttribute("aria-pressed", String(selected));
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

function productCard(group) {
    const card = node("article", "product-card");
    card.append(node("h3", "", group.name));
    const picker = node("select");
    const label = node("label", "variant-field");
    const field = node("span", "select-field variant-picker");
    label.append(node("span", "variant-label", "Varian"), field);
    field.append(picker);
    const details = node("div", "variant-details");
    details.setAttribute("aria-live", "polite");
    const actions = node("div", "actions");
    const variants = new Map(group.variants.map((product) => [product.id, product]));
    for (const product of variants.values()) {
        const title = [product.color, formatSize(product.size), stockInfo(product.stock)[0]].filter(Boolean).join(" · ");
        picker.add(new Option(title, product.id));
    }
    const selected = selectedVariants.get(group.key);
    const defaultVariant = group.variants.find(available) || group.variants[0];
    picker.value = variants.has(selected) ? selected : defaultVariant.id;

    function displayVariant() {
        const product = variants.get(picker.value);
        details.replaceChildren(stockPill(product), node("span", "price", product.price || "Harga belum tersedia"));
        actions.replaceChildren(...variantActionButtons(product, "stock"));
    }

    picker.addEventListener("change", () => {
        selectedVariants.set(group.key, picker.value);
        displayVariant();
    });
    displayVariant();
    card.append(label, details, actions);
    return card;
}

function empty(list, message) {
    $(list).replaceChildren(node("p", "empty", message));
}

function colorKey(value) {
    return value ? value.toLocaleLowerCase("id") : "";
}

function groupMatchingProducts() {
    const terms = $("query").value.trim().toLocaleLowerCase("id").split(/\s+/).filter(Boolean);
    const color = $("color").value;
    const size = $("size").value;
    const status = $("status").value;
    const groups = new Map();

    for (const product of products) {
        const keywords = `${product.name} ${product.color || ""} ${product.size}`.toLocaleLowerCase("id");
        if ((color && colorKey(product.color) !== color)
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

function showCarouselPosition() {
    $("carousel-controls").hidden = filteredGroups.length < 2;
    $("previous").disabled = carouselIndex === 0;
    $("next").disabled = carouselIndex >= filteredGroups.length - 1;
    $("carousel-position").textContent = `${carouselIndex + 1} / ${filteredGroups.length}`;
    for (const [index, card] of Array.from($("stock-list").children).entries()) {
        card.toggleAttribute("inert", index !== carouselIndex);
    }
}

function moveCarousel(index) {
    if (!filteredGroups.length) return;
    carouselIndex = Math.max(0, Math.min(index, filteredGroups.length - 1));
    if (carouselIndex + 3 >= $("stock-list").children.length) appendCards(carouselIndex + CARD_BATCH);
    $("stock-list").scrollLeft = cardOffset(carouselIndex);
    showCarouselPosition();
}

function onCarouselScroll() {
    const carousel = $("stock-list");
    if (carousel.children.length < 2) return;
    const spacing = cardOffset(1);
    if (spacing <= 0) return;
    const index = Math.max(0, Math.min(carousel.children.length - 1, Math.round(carousel.scrollLeft / spacing)));
    if (index === carouselIndex) return;
    carouselIndex = index;
    if (index + 3 >= carousel.children.length) appendCards(index + CARD_BATCH);
    showCarouselPosition();
}

function renderStock() {
    const carousel = $("stock-list");
    filteredGroups = groupMatchingProducts();
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

function setProducts(items) {
    products = items;
    const sizes = Array.from(new Set(products.map(({size}) => size)));
    sizes.sort((left, right) => {
        const rank = (size) => sizeOrder.includes(size) ? sizeOrder.indexOf(size) : sizeOrder.length;
        return rank(left) - rank(right) || left.localeCompare(right, "id", {numeric: true});
    });
    setFilterOptions("size", "Semua ukuran", sizes, formatSize);
    const colors = new Map(products.filter(({color}) => color).map(({color}) => [colorKey(color), color]));
    const orderedColors = Array.from(colors.keys()).sort((left, right) => left.localeCompare(right, "id"));
    setFilterOptions("color", "Semua warna", orderedColors, (key) => colors.get(key));
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
        $("stock-time").textContent = demo ? "Data contoh untuk mencoba dashboard." : update ? `Diperbarui ${update}` : "Snapshot stok belum tersedia.";
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
for (const id of ["query", "color", "size", "status"]) {
    $(id).addEventListener(id === "query" ? "input" : "change", () => {
        carouselIndex = 0;
        renderStock();
    });
}
$("stock-list").addEventListener("scroll", onCarouselScroll, {passive: true});
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
