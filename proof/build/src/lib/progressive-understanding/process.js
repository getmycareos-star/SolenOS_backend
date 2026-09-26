"use strict";
/**
 * Progressive Understanding Engine — process one observation against ACS state.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.processProgressiveUnderstanding = processProgressiveUnderstanding;
const detect_signals_1 = require("./detect-signals");
const resolve_uncertainty_1 = require("./resolve-uncertainty");
const evolve_synthesis_1 = require("./evolve-synthesis");
const questions_1 = require("./questions");
const clarity_pillars_1 = require("./clarity-pillars");
const turn_copy_1 = require("./turn-copy");
const classify_1 = require("../active-care-situation/classify");
function processProgressiveUnderstanding(input) {
    const { prior, relation, observation, kind, rawText, draft } = input;
    const priorSignals = prior
        ? (0, detect_signals_1.collectSituationSignals)(prior.observations)
        : [];
    const allSignals = (0, detect_signals_1.collectSituationSignals)(draft.observations);
    const latestSignals = (0, detect_signals_1.latestObservationSignals)(draft.observations);
    const latestSignalsDetected = (0, detect_signals_1.detectObservationSignals)(rawText, kind);
    const newSignals = latestSignalsDetected.filter((s) => !priorSignals.includes(s) && s !== "general");
    const introducedDimension = (latestSignals.includes("appetite") &&
        priorSignals.some((s) => s !== "appetite" && s !== "general")) ||
        (latestSignals.includes("medication") && prior != null) ||
        (latestSignals.includes("fall") &&
            prior != null &&
            prior.theme === "emotional_behavior");
    const { remaining, resolved } = (0, resolve_uncertainty_1.resolveAnsweredUncertainties)({
        openQuestions: prior?.open_questions ?? [],
        rawText,
    });
    const patternLabel = (0, detect_signals_1.patternLabelFor)(allSignals, latestSignals);
    const understanding_stage = (0, evolve_synthesis_1.stageFromSignals)(draft.observations.length, allSignals);
    const improvement = (0, detect_signals_1.isImprovementUpdate)(latestSignals);
    let theme = draft.theme;
    if ((0, detect_signals_1.emotionalSignalCount)(allSignals) >= 1 ||
        (0, classify_1.situationThemeFor)(kind, rawText) === "emotional_behavior") {
        theme =
            prior?.theme === "incident" && introducedDimension && latestSignals.includes("fall")
                ? "mixed"
                : (0, detect_signals_1.emotionalSignalCount)(allSignals) >= 1
                    ? "emotional_behavior"
                    : draft.theme;
    }
    const priorMatters = prior?.what_matters_now ?? null;
    const compoundSignal = (0, detect_signals_1.detectCompoundSignal)(draft.observations);
    const trajectoryByDomain = new Map();
    const nonGeneralSignals = allSignals.filter((s) => s !== "general");
    for (const obs of draft.observations) {
        const obsSignals = (0, detect_signals_1.detectObservationSignals)(obs.raw_text, obs.kind).filter((s) => s !== "general");
        for (const signal of obsSignals) {
            const current = trajectoryByDomain.get(signal) ?? "unknown";
            trajectoryByDomain.set(signal, (0, detect_signals_1.signalTrajectory)(draft.observations, signal));
        }
    }
    const crossSignalCorrelations = [];
    for (let i = 0; i < nonGeneralSignals.length; i++) {
        for (let j = i + 1; j < nonGeneralSignals.length; j++) {
            const correlation = (0, detect_signals_1.computeCrossSignalCorrelation)(draft.observations, nonGeneralSignals[i], nonGeneralSignals[j]);
            if (correlation !== "independent") {
                crossSignalCorrelations.push({
                    signal_a: nonGeneralSignals[i],
                    signal_b: nonGeneralSignals[j],
                    correlation,
                });
            }
        }
    }
    const claritySufficient = (0, questions_1.understandingSufficient)({
        situation: { ...draft, theme, understanding_stage },
        signals: allSignals,
    });
    const clarity = claritySufficient
        ? (0, clarity_pillars_1.buildCareClarityPillars)({
            situation: { ...draft, theme, understanding_stage },
            stage: understanding_stage,
            signals: allSignals,
            latestSignals,
            patternLabel,
            kind,
        })
        : {
            what_matters_now: null,
            what_can_wait: null,
            what_may_become_serious: null,
        };
    const what_matters_now = clarity.what_matters_now;
    const what_can_wait = clarity.what_can_wait;
    const what_may_become_serious = clarity.what_may_become_serious;
    const mattersChanged = Boolean(what_matters_now && what_matters_now !== priorMatters && prior != null);
    const effect = (0, turn_copy_1.classifyUnderstandingEffect)({
        relation,
        resolvedCount: resolved.length,
        newSignalCount: newSignals.length,
        introducedDimension,
        mattersChanged,
        stage: understanding_stage,
        priorStage: prior?.understanding_stage ?? null,
        invalidatesPrior: improvement ||
            (Boolean(prior?.synthesis) &&
                Boolean(patternLabel) &&
                patternLabel !== prior?.pattern_label &&
                understanding_stage === "synthesizing"),
    });
    const synthesis = (0, evolve_synthesis_1.evolveSynthesis)({
        situation: { ...draft, theme, understanding_stage },
        stage: understanding_stage,
        signals: allSignals,
        latestSignals,
        patternLabel,
    });
    const what_changed_in_understanding = (0, evolve_synthesis_1.evolveUnderstandingDelta)({
        prior,
        stage: understanding_stage,
        signals: allSignals,
        priorSignals,
        patternLabel,
        resolvedCount: resolved.length,
        effectLabel: effect,
        latestSignals,
        latestRawText: rawText,
    });
    const open_questions = improvement || (0, resolve_uncertainty_1.isCaregiverQuestionPushback)(rawText)
        ? []
        : (0, questions_1.nextQuestionsForUnderstanding)({
            situation: {
                ...draft,
                theme,
                understanding_stage,
                open_questions: remaining,
                asked_questions: prior?.asked_questions ?? draft.asked_questions,
            },
            stage: understanding_stage,
            latestKind: kind,
            latestText: rawText,
            signals: allSignals,
            patternLabel,
            remainingOpen: remaining,
        });
    // Only NEW asks this turn — never re-surface prior unanswered asks (trust).
    // Remaining known-unknowns still persist on ACS/CRS for soft return invite (Locked B).
    const known_unknowns = (0, resolve_uncertainty_1.mergeKnownUnknowns)(remaining, open_questions);
    // Track every question shown so the next turn never repeats the same ask.
    const asked_questions = [
        ...(prior?.asked_questions ?? draft.asked_questions),
        ...open_questions.filter((q) => !(prior?.asked_questions ?? draft.asked_questions).some((a) => a.toLowerCase() === q.toLowerCase())),
    ].slice(-24);
    const connection_note = (0, turn_copy_1.connectionNoteForProgress)({
        relation,
        stage: understanding_stage,
        count: draft.observations.length,
        effect,
    });
    const current_understanding = (0, evolve_synthesis_1.buildEvolvingUnderstandingLines)({
        ...draft,
        theme,
        understanding_stage,
    }).filter(clarity_pillars_1.isCaregiverFacingFactLine);
    // Prefer observation human_facts; ensure at least latest fact present when it is caregiver-facing
    if (observation.human_fact &&
        (0, clarity_pillars_1.isCaregiverFacingFactLine)(observation.human_fact) &&
        !current_understanding.some((l) => l.toLowerCase() === observation.human_fact.toLowerCase())) {
        current_understanding.push(observation.human_fact.endsWith(".")
            ? observation.human_fact
            : `${observation.human_fact}.`);
    }
    const copy = (0, turn_copy_1.buildProgressiveTurnCopy)({
        relation,
        stage: understanding_stage,
        effect,
        observationCount: draft.observations.length,
        whatChanged: what_changed_in_understanding,
        patternLabel,
    });
    const show_attention_sections = claritySufficient;
    return {
        understanding_stage,
        theme,
        open_questions,
        known_unknowns,
        asked_questions,
        resolved_uncertainties: resolved,
        connection_note,
        synthesis,
        what_matters_now,
        what_can_wait,
        what_may_become_serious,
        current_understanding: current_understanding.slice(0, 4),
        understanding_heading: copy.understanding_heading,
        confirmation_title: copy.confirmation_title,
        confirmation_body: copy.confirmation_body,
        insufficiency_note: copy.insufficiency_note,
        what_will_be_remembered: (0, questions_1.rememberedThemesForUnderstanding)({ ...draft, theme }, patternLabel),
        show_attention_sections,
        what_changed_in_understanding,
        effect,
        signals_present: allSignals,
        pattern_label: patternLabel,
        compound_signal: compoundSignal,
        trajectory_by_domain: Object.fromEntries(trajectoryByDomain),
        cross_signal_correlations: crossSignalCorrelations,
    };
}
