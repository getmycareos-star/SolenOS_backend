"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __exportStar = (this && this.__exportStar) || function(m, exports) {
    for (var p in m) if (p !== "default" && !Object.prototype.hasOwnProperty.call(exports, p)) __createBinding(exports, m, p);
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.OllamaProvider = void 0;
exports.getLlmProvider = getLlmProvider;
exports.resetLlmProvider = resetLlmProvider;
exports.invokeLlm = invokeLlm;
const ollama_1 = require("./ollama");
var ollama_2 = require("./ollama");
Object.defineProperty(exports, "OllamaProvider", { enumerable: true, get: function () { return ollama_2.OllamaProvider; } });
__exportStar(require("./env"), exports);
let _provider = null;
function getLlmProvider() {
    if (!_provider) {
        _provider = new ollama_1.OllamaProvider();
    }
    return _provider;
}
function resetLlmProvider() {
    _provider = null;
}
async function invokeLlm(request) {
    return getLlmProvider().invoke(request);
}
