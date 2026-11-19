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
let visible = 12;
let loaded = false;
let activeView = "stock";
let feedbackTimer;

const examples = [
    ["Sasmita Navy", "L", "Aman", "Rp 149.000"],
    ["Sasmita Navy", "XL", "Habis", "Rp 149.000"],
    ["Aruna Sage", "M", "Ready", "Rp 164.000"],
    ["Aruna Sage", "L", "Aman", "Rp 164.000"],
    ["Kirana Mauve", "M", "Aman", "Rp 159.000"],
    ["Kirana Mauve", "L", "Habis", "Rp 159.000"],
    ["Nala Sand", "ALL SIZE", "Ready", "Rp 99.000"],
    ["Alea Mocha", "ALL SIZE", "Aman", "Rp 119.000"],
].map(([name, size, stock, price]) => ({id: `${name} SIZE ${size}`, name, size, stock, price}));

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

function swatchColor(name) {
    if (/navy|blue|denim/i.test(name)) return "blue";
    if (/mauve|rose|pink|lilac/i.test(name)) return "rose";
    if (/sand|mocha|brown|cream/i.test(name)) return "earth";
    return "";
}

function productRow(product, context, lastRestock = null) {
    const row = node("article", "product");
    const swatch = node("div", `swatch ${swatchColor(product.name)}`, product.name.charAt(0).toUpperCase());
    swatch.setAttribute("aria-hidden", "true");
    const info = node("div");
    info.append(node("h3", "", product.name));
    const meta = node("div", "meta");
    const [label, pill] = stockInfo(product.stock);
    meta.append(node("span", "", formatSize(product.size)), node("span", `pill ${pill}`, label));
    if (context === "watch") {
        const date = formatDate(lastRestock);
        meta.append(node("span", "", date ? `Restock ${date}` : "Belum ada riwayat restock"));
    }
    info.append(meta);
    const options = node("div", "actions");
    if (context !== "catalog") {
        const watched = watchlist.has(product.id);
        const toggle = action(context === "watch" ? "Hapus pantauan" : watched ? "Dipantau" : "Pantau",
            (button) => toggleWatch(product, button, !watched), watched && context === "stock");
        if (context === "stock") toggle.setAttribute("aria-pressed", String(watched));
        options.append(toggle);
    }
    if ((context === "stock" && available(product)) || context === "catalog") {
        const selected = selection.has(product.id);
        options.append(action(context === "catalog" ? "Hapus" : selected ? "Dipilih" : "Ke katalog",
            () => toggleProduct(product), selected && context === "stock"));
    }
    row.append(swatch, info, node("div", "price", product.price || "—"), options);
    return row;
}

function empty(list, message) {
    $(list).replaceChildren(node("p", "empty", message));
}

function renderStock() {
    const query = $("query").value.trim().toLowerCase();
    const size = $("size").value;
    const status = $("status").value;
    const terms = query.split(/\s+/).filter(Boolean);
    const matches = products.filter((product) => (
        terms.every((term) => product.id.toLowerCase().includes(term))
        && (!size || product.size === size)
        && (!status || (status === "available" ? available(product) : product.stock.toLowerCase() === status))
    ));
    $("more").hidden = matches.length <= visible;
    if (!matches.length) {
        empty("stock-list", loaded ? "Tidak ada varian yang cocok." : "Stok belum dimuat.");
        return;
    }
    $("stock-list").replaceChildren(...matches.slice(0, visible).map((product) => productRow(product, "stock")));
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

function setProducts(items) {
    products = items;
    const sizes = Array.from(new Set(products.map(({size}) => size)));
    sizes.sort((left, right) => {
        const rank = (size) => sizeOrder.includes(size) ? sizeOrder.indexOf(size) : sizeOrder.length;
        return rank(left) - rank(right) || left.localeCompare(right, "id", {numeric: true});
    });
    const options = sizes.map((size) => {
        const option = node("option", "", formatSize(size));
        option.value = size;
        return option;
    });
    const previous = $("size").value;
    $("size").replaceChildren(new Option("Semua ukuran", ""), ...options);
    $("size").value = sizes.includes(previous) ? previous : "";
}

async function load() {
    notice("");
    $("refresh").disabled = true;
    try {
        const data = demo ? {products: examples, watchlist: []} : await api("dashboard");
        setProducts(data.products);
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
                .map((item) => `• ${item.name} (${formatSize(item.size)}) — ${item.price}`);
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
for (const id of ["query", "size", "status"]) {
    $(id).addEventListener(id === "query" ? "input" : "change", () => { visible = 12; renderStock(); });
}
$("refresh").addEventListener("click", load);
$("more").addEventListener("click", () => { visible += 12; renderStock(); });
$("make-catalog").addEventListener("click", makeCatalog);
$("copy").addEventListener("click", copyCatalog);

if (demo) {
    $("mode").classList.add("is-demo");
    $("mode").replaceChildren(node("span"), document.createTextNode("Mode demo"));
} else {
    $("refresh").hidden = false;
    telegram.ready();
    telegram.expand();
}
load();
