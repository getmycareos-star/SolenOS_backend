"use strict";
/**
 * Filesystem durability for Living Care Record spine (mirrors consent-store).
 * Maps are cache; `.data/` JSON is source of truth across process restarts.
 *
 * Client bundles must never resolve `node:fs`. This module uses Node `fs`/`path`
 * only on the server; browser callers get no-ops (in-memory Maps remain source
 * of truth for that tab).
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.livingCareRecordDataDir = livingCareRecordDataDir;
exports.sanitizeDurableCareKey = sanitizeDurableCareKey;
exports.readDurableJson = readDurableJson;
exports.writeDurableJson = writeDurableJson;
exports.deleteDurableFile = deleteDurableFile;
exports.clearDurableDirectory = clearDurableDirectory;
exports.listDurableDirectory = listDurableDirectory;
const fs_1 = require("fs");
const path_1 = require("path");
const isBrowser = typeof window !== "undefined";
function livingCareRecordDataDir(...parts) {
    if (isBrowser)
        return ["browser-data", ...parts].join("/");
    return (0, path_1.join)(process.cwd(), ".data", ...parts);
}
function sanitizeDurableCareKey(careKey) {
    const cleaned = careKey.replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 120);
    return cleaned || "default_caregiver";
}
function readDurableJson(filePath) {
    if (isBrowser)
        return null;
    try {
        if (!(0, fs_1.existsSync)(filePath))
            return null;
        return JSON.parse((0, fs_1.readFileSync)(filePath, "utf8"));
    }
    catch {
        return null;
    }
}
function writeDurableJson(filePath, value) {
    if (isBrowser)
        return;
    try {
        (0, fs_1.mkdirSync)((0, path_1.join)(filePath, ".."), { recursive: true });
        (0, fs_1.writeFileSync)(filePath, JSON.stringify(value, null, 2), "utf8");
    }
    catch {
        // Non-fatal — cache remains for this process.
    }
}
function deleteDurableFile(filePath) {
    if (isBrowser)
        return;
    try {
        if ((0, fs_1.existsSync)(filePath))
            (0, fs_1.rmSync)(filePath, { force: true });
    }
    catch {
        // Non-fatal
    }
}
function clearDurableDirectory(dirPath) {
    if (isBrowser)
        return;
    try {
        if (!(0, fs_1.existsSync)(dirPath))
            return;
        for (const name of (0, fs_1.readdirSync)(dirPath)) {
            (0, fs_1.rmSync)((0, path_1.join)(dirPath, name), { force: true, recursive: true });
        }
    }
    catch {
        // Non-fatal
    }
}
/** List basename files in a durable data directory (server only). */
function listDurableDirectory(dirPath) {
    if (isBrowser)
        return [];
    try {
        if (!(0, fs_1.existsSync)(dirPath))
            return [];
        return (0, fs_1.readdirSync)(dirPath);
    }
    catch {
        return [];
    }
}
