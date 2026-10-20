"""
Caregiver Intent Understanding Service for enhanced NLP to better interpret queries and emotional states.
Builds upon existing clinical NER service to provide intent classification, sentiment analysis, and query understanding.
"""
import re
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple, Union
from enum import Enum
import json

try:
    import spacy
    from spacy.matcher import Matcher
    SPACY_AVAILABLE = True
except ImportError:
    SPACY_AVAILABLE = False

try:
    from textblob import TextBlob
    TEXTBLOB_AVAILABLE = True
except ImportError:
    TEXTBLOB_AVAILABLE = False

try:
    import nltk
    from nltk.sentiment import SentimentIntensityAnalyzer
    NLTK_AVAILABLE = True
    # Download required NLTK data if not present
    try:
        nltk.data.find('sentiment/vader_lexicon.zip')
    except LookupError:
        nltk.download('vader_lexicon', quiet=True)
except ImportError:
    NLTK_AVAILABLE = False

logger = logging.getLogger(__name__)

class IntentType(Enum):
    """Types of caregiver intents that can be detected."""
    INFORMATION_SEEKING = "information_seeking"
    ACTION_REQUEST = "action_request"
    EMOTIONAL_EXPRESSION = "emotional_expression"
    CONCERN_EXPRESSION = "concern_expression"
    REASSURANCE_SEEKING = "reassurance_seeking"
    INSTRUCTION_FOLLOWING = "instruction_following"
    QUESTION_CLARIFICATION = "question_clarification"
    COMPLAINT = "complaint"
    THANKS_GRATITUDE = "thanks_gratitude"
    GREETING = "greeting"
    FAREWELL = "farewell"
    UNCLEAR = "unclear"

class EmotionType(Enum):
    """Types of emotions that can be detected in caregiver expressions."""
    ANXIETY = "anxiety"
    FRUSTRATION = "frustration"
    WORRY = "worry"
    SADNESS = "sadness"
    ANGER = "anger"
    GUILT = "guilt"
    HOPE = "hope"
    RELIEF = "relief"
    GRATITUDE = "gratitude"
    LOVE_AFFECTION = "love_affection"
    EMPATHY = "empathy"
    OVERWHELMED = "overwhelmed"
    EXHAUSTION = "exhaustion"
    NEUTRAL = "neutral"
    POSITIVE = "positive"
    NEGATIVE = "negative"

class CaregiverIntentService:
    """Service for understanding caregiver intent and emotional state from text."""
    
    def __init__(self):
        """Initialize the Caregiver Intent Understanding service."""
        self.nlp = None
        self.matcher = None
        self.sia = None
        self._init_nlp_components()
        logger.info("Caregiver Intent Understanding service initialized")
    
    def _init_nlp_components(self):
        """Initialize NLP components."""
        # Initialize spaCy if available
        if SPACY_AVAILABLE:
            try:
                self.nlp = spacy.load("en_core_web_sm")
                self.matcher = Matcher(self.nlp.vocab)
                self._setup_intent_patterns()
                logger.info("spaCy components initialized")
            except Exception as e:
                logger.warning(f"Could not initialize spaCy: {e}")
                self.nlp = None
        
        # Initialize NLTK sentiment analyzer if available
        if NLTK_AVAILABLE:
            try:
                self.sia = SentimentIntensityAnalyzer()
                logger.info("NLTK sentiment analyzer initialized")
            except Exception as e:
                logger.warning(f"Could not initialize NLTK sentiment analyzer: {e}")
                self.sia = None
    
    def _setup_intent_patterns(self):
        """Setup regex patterns for intent detection."""
        if not self.matcher:
            return
        
        # Information seeking patterns
        info_patterns = [
            [{"LOWER": {"IN": ["what", "how", "why", "when", "where", "who"]}}, {"OP": "*"}],
            [{"LOWER": "can"}, {"LOWER": "you"}, {"OP": "*"}, {"LOWER": {"IN": ["tell", "explain", "describe"]}}],
            [{"LOWER": "i"}, {"LOWER": "need"}, {"OP": "*"}, {"LOWER": {"IN": ["to", "know", "understand"]}}],
            [{"LOWER": "is"}, {"LOWER": "it"}, {"OP": "*"}, {"LOWER": {"IN": ["normal", "okay", "safe"]}}],
        ]
        self.matcher.add("INFORMATION_SEEKING", info_patterns)
        
        # Action request patterns
        action_patterns = [
            [{"LOWER": {"IN": ["please", "could", "would", "can"]}}, {"OP": "*"}, 
             {"LOWER": {"IN": ["help", "do", "make", "get", "find", "schedule", "call"]}}, {"OP": "*"}],
            [{"LOWER": "i"}, {"LOWER": "need"}, {"OP": "*"}, {"LOWER": {"IN": ["help", "assistance", "support"]}}],
            [{"LOWER": "will"}, {"LOWER": "you"}, {"OP": "*"}, {"LOWER": {"IN": ["help", "assist", "support"]}}],
        ]
        self.matcher.add("ACTION_REQUEST", action_patterns)
        
        # Emotional expression patterns
        emotion_patterns = [
            [{"LOWER": {"IN": ["i", "am", "i'm", "feel", "feeling"]}}, {"OP": "*"}, 
             {"LOWER": {"IN": ["worried", "anxious", "scared", "afraid", "nervous", "stressed", "overwhelmed"]}}],
            [{"LOWER": {"IN": ["i", "am", "i'm"]}}, {"OP": "*"}, 
             {"LOWER": {"IN": ["frustrated", "annoyed", "angry", "upset", "irritated"]}}],
            [{"LOWER": {"IN": ["i", "am", "i'm"]}}, {"OP": "*"}, 
             {"LOWER": {"IN": ["sad", "unhappy", "depressed", "down", "blue"]}}],
            [{"LOWER": {"IN": ["i", "am", "i'm"]}}, {"OP": "*"}, 
             {"LOWER": {"IN": ["grateful", "thankful", "appreciative", "thanks"]}}],
        ]
        self.matcher.add("EMOTIONAL_EXPRESSION", emotion_patterns)
        
        # Concern expression patterns
        concern_patterns = [
            [{"LOWER": "i"}, {"LOWER": "worry"}, {"OP": "*"}, {"LOWER": {"IN": ["about", "that", "if"]}}],
            [{"LOWER": "what"}, {"LOWER": "if"}, {"OP": "*"}],
            [{"LOWER": "i"}, {"LOWER": "am"}, {"OP": "*"}, {"LOWER": "concerned"}, {"OP": "*"}, {"LOWER": {"IN": ["about", "for"]}}],
            [{"LOWER": "is"}, {"LOWER": "there"}, {"OP": "*"}, {"LOWER": {"IN": ["a", "any"]}}, {"OP": "*"}, 
             {"LOWER": {"IN": ["risk", "danger", "problem", "issue", "complication"]}}],
        ]
        self.matcher.add("CONCERN_EXPRESSION", concern_patterns)
        
        # Reassurance seeking patterns
        reassurance_patterns = [
            [{"LOWER": "is"}, {"LOWER": "it"}, {"OP": "*"}, {"LOWER": {"IN": ["okay", "alright", "fine", "safe", "normal"]}}],
            [{"LOWER": "should"}, {"LOWER": "i"}, {"OP": "*"}, {"LOWER": {"IN": ["be", "feeling", "worrying"]}}],
            [{"LOWER": "am"}, {"LOWER": "i"}, {"OP": "*"}, {"LOWER": {"IN": ["overreacting", "being", "too"]}}, {"OP": "*"}, 
             {"LOWER": {"IN": ["worried", "concerned", "anxious"]}}],
            [{"LOWER": "tell"}, {"LOWER": "me"}, {"OP": "*"}, {"LOWER": {"IN": ["the", "truth", "what", "really"]}}],
        ]
        self.matcher.add("REASSURANCE_SEEKING", reassurance_patterns)
        
        # Thanks/gratitude patterns
        thanks_patterns = [
            [{"LOWER": {"IN": ["thank", "thanks", "thankful", "grateful", "appreciate"]}}, {"OP": "*"}],
            [{"LOWER": "i"}, {"LOWER": "appreciate"}, {"OP": "*"}],
            [{"LOWER": "thanks"}, {"LOWER": "for"}, {"OP": "*"}],
        ]
        self.matcher.add("THANKS_GRATITUDE", thanks_patterns)
        
        # Greeting patterns
        greeting_patterns = [
            [{"LOWER": {"IN": ["hello", "hi", "hey", "good", "morning", "afternoon", "evening"]}}, {"OP": "*"}],
        ]
        self.matcher.add("GREETING", greeting_patterns)
        
        # Complaint patterns
        complaint_patterns = [
            [{"LOWER": {"IN": ["not", "doesn't", "didn't", "won't"]}}, {"OP": "*"}, 
             {"LOWER": {"IN": ["working", "helping", "effective", "enough"]}}],
            [{"LOWER": {"IN": ["problem", "issue", "trouble", "difficulty", "hard"]}}, {"OP": "*"}],
            [{"LOWER": {"IN": ["frustrated", "annoyed", "upset", "angry"]}}, {"OP": "*"}, 
             {"LOWER": {"IN": ["with", "about", "of"]}}],
        ]
        self.matcher.add("COMPLAINT", complaint_patterns)
    
    def understand_intent_and_emotion(self, text: str) -> Dict[str, Any]:
        """
        Understand caregiver intent and emotional state from text.
        
        Args:
            text: Input text from caregiver
            
        Returns:
            Dictionary containing intent, emotion, sentiment scores, and analysis details
        """
        if not text or not text.strip():
            return self._get_default_result()
        
        # Clean and preprocess text
        cleaned_text = self._preprocess_text(text)
        
        # Initialize result
        result = {
            "text": text,
            "cleaned_text": cleaned_text,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "intent": IntentType.UNCLEAR.value,
            "intent_confidence": 0.0,
            "emotion": EmotionType.NEUTRAL.value,
            "emotion_confidence": 0.0,
            "sentiment": {
                "polarity": 0.0,
                "subjectivity": 0.0,
                "label": "neutral"
            },
            "detailed_scores": {},
            "keywords": [],
            "entities": [],
            "analysis_details": {}
        }
        
        try:
            # Extract entities using clinical NER if available
            entities = self._extract_entities(cleaned_text)
            result["entities"] = entities
            
            # Detect intent
            intent_result = self._detect_intent(cleaned_text)
            result["intent"] = intent_result["intent"]
            result["intent_confidence"] = intent_result["confidence"]
            
            # Detect emotion
            emotion_result = self._detect_emotion(cleaned_text)
            result["emotion"] = emotion_result["emotion"]
            result["emotion_confidence"] = emotion_result["confidence"]
            
            # Analyze sentiment
            sentiment_result = self._analyze_sentiment(cleaned_text)
            result["sentiment"] = sentiment_result
            
            # Extract keywords
            result["keywords"] = self._extract_keywords(cleaned_text)
            
            # Generate analysis details
            result["analysis_details"] = self._generate_analysis_details(
                cleaned_text, intent_result, emotion_result, sentiment_result, entities
            )
            
        except Exception as e:
            logger.error(f"Error in intent/emotion analysis: {e}")
            result["error"] = str(e)
        
        return result
    
    def _preprocess_text(self, text: str) -> str:
        """Preprocess text for analysis."""
        # Convert to lowercase
        text = text.lower().strip()
        # Remove extra whitespace
        text = re.sub(r'\s+', ' ', text)
        # Remove special characters but keep basic punctuation
        text = re.sub(r'[^\w\s\.\,\!\?\:\;\-\(\)]', '', text)
        return text
    
    def _extract_entities(self, text: str) -> List[Dict[str, Any]]:
        """Extract entities using clinical NER if available."""
        entities = []
        if self.nlp:
            try:
                doc = self.nlp(text)
                for ent in doc.ents:
                    entities.append({
                        "text": ent.text,
                        "label": ent.label_,
                        "start": ent.start_char,
                        "end": ent.end_char,
                        "confidence": 0.8  # Default confidence for spaCy entities
                    })
            except Exception as e:
                logger.warning(f"Could not extract entities: {e}")
        return entities
    
    def _detect_intent(self, text: str) -> Dict[str, Any]:
        """Detect caregiver intent from text."""
        if not self.matcher or not self.nlp:
            # Fallback to rule-based intent detection
            return self._rule_based_intent_detection(text)
        
        try:
            doc = self.nlp(text)
            matches = self.matcher(doc)
            
            # Count matches by intent type
            intent_counts = {}
            for match_id, start, end in matches:
                intent_label = self.nlp.vocab.strings[match_id]
                intent_counts[intent_label] = intent_counts.get(intent_label, 0) + 1
            
            # Determine most likely intent
            if intent_counts:
                top_intent = max(intent_counts, key=intent_counts.get)
                total_matches = sum(intent_counts.values())
                confidence = min(0.95, intent_counts[top_intent] / total_matches + 0.3)
                
                # Map intent label to IntentType enum
                try:
                    intent_type = IntentType[top_intent]
                    return {
                        "intent": intent_type.value,
                        "confidence": confidence
                    }
                except KeyError:
                    # If the matched label doesn't correspond to an IntentType, fall back
                    pass
            
            # Fallback to rule-based if spaCy matching didn't work
            return self._rule_based_intent_detection(text)
            
        except Exception as e:
            logger.warning(f"Error in spaCy intent detection: {e}")
            return self._rule_based_intent_detection(text)
    
    def _rule_based_intent_detection(self, text: str) -> Dict[str, Any]:
        """Rule-based fallback for intent detection."""
        text_lower = text.lower()
        
        # Define intent keywords
        intent_keywords = {
            IntentType.INFORMATION_SEEKING: [
                "what", "how", "why", "when", "where", "who", "explain", "tell me", 
                "describe", "information", "know", "understand", "clarify", "details"
            ],
            IntentType.ACTION_REQUEST: [
                "please", "help", "can you", "could you", "would you", "need", 
                "require", "assist", "support", "do", "make", "get", "find", "schedule"
            ],
            IntentType.EMOTIONAL_EXPRESSION: [
                "i feel", "i am", "i'm", "feeling", "worried", "anxious", "scared", 
                "afraid", "nervous", "stressed", "overwhelmed", "frustrated", "angry",
                "upset", "sad", "unhappy", "depressed", "grateful", "thankful"
            ],
            IntentType.CONCERN_EXPRESSION: [
                "worry", "concerned", "what if", "afraid that", "scared that",
                "is there any", "is there a", "risk", "danger", "problem", "issue"
            ],
            IntentType.REASSURANCE_SEEKING: [
                "is it okay", "is it alright", "is it normal", "should i", "am i",
                "overreacting", "being too", "tell me the truth", "really"
            ],
            IntentType.THANKS_GRATITUDE: [
                "thank", "thanks", "thankful", "grateful", "appreciate", "appreciation"
            ],
            IntentType.GREETING: [
                "hello", "hi", "hey", "good morning", "good afternoon", "good evening"
            ],
            IntentType.COMPLAINT: [
                "not working", "doesn't help", "not effective", "problem", "issue",
                "trouble", "difficulty", "frustrated", "annoyed", "upset", "angry"
            ]
        }
        
        # Score each intent
        intent_scores = {}
        for intent_type, keywords in intent_keywords.items():
            score = 0
            for keyword in keywords:
                if keyword in text_lower:
                    score += 1
            intent_scores[intent_type] = score
        
        # Find intent with highest score
        if intent_scores and max(intent_scores.values()) > 0:
            top_intent = max(intent_scores, key=intent_scores.get)
            max_score = intent_scores[top_intent]
            total_keywords = sum(len(keywords) for keywords in intent_keywords.values())
            # Normalize confidence based on keyword matches
            confidence = min(0.8, 0.3 + (max_score / len(intent_keywords[top_intent])) * 0.5)
            return {
                "intent": top_intent.value,
                "confidence": confidence
            }
        
        # Default to unclear
        return {
            "intent": IntentType.UNCLEAR.value,
            "confidence": 0.2
        }
    
    def _detect_emotion(self, text: str) -> Dict[str, Any]:
        """Detect emotion from text."""
        if not self.nlp:
            # Fallback to rule-based emotion detection
            return self._rule_based_emotion_detection(text)
        
        try:
            # Use spaCy for emotion detection if possible
            doc = self.nlp(text)
            
            # Check for emotion keywords in the text
            emotion_indicators = {
                EmotionType.ANXIETY: ["worried", "anxious", "nervous", "stressed", "uneasy", "apprehensive"],
                EmotionType.FRUSTRATION: ["frustrated", "annoyed", "irritated", "exasperated", "impatient"],
                EmotionType.WORRY: ["worry", "worried", "concerned", "troubled", "disturbed"],
                EmotionType.SADNESS: ["sad", "unhappy", "depressed", "down", "blue", "melancholy"],
                EmotionType.ANGER: ["angry", "mad", "furious", "irate", "livid", "outraged"],
                EmotionType.GUILT: ["guilty", "guilt", "remorseful", "regretful", "contrite"],
                EmotionType.HOPE: ["hopeful", "hope", "optimistic", "encouraged", "positive"],
                EmotionType.RELIEF: ["relieved", "relief", "better", "improved", "easier"],
                EmotionType.GRATITUDE: ["grateful", "thankful", "thanks", "appreciative", "thank you"],
                EmotionType.LOVE_AFFECTION: ["love", "love you", "care about", "fond", "affectionate"],
                EmotionType.EMPATHY: ["understand", "feel for", "sympathize", "compassionate"],
                EmotionType.OVERWHELMED: ["overwhelmed", "too much", "can't handle", "swamped", "buried"],
                EmotionType.EXHAUSTION: ["exhausted", "tired", "worn out", "drained", "fatigued", "weary"]
            }
            
            emotion_scores = {}
            for emotion_type, keywords in emotion_indicators.items():
                score = 0
                for keyword in keywords:
                    if keyword in text.lower():
                        score += 1
                emotion_scores[emotion_type] = score
            
            # Find emotion with highest score
            if emotion_scores and max(emotion_scores.values()) > 0:
                top_emotion = max(emotion_scores, key=emotion_scores.get)
                max_score = emotion_scores[top_emotion]
                total_keywords = sum(len(keywords) for keywords in emotion_indicators.values())
                # Normalize confidence
                confidence = min(0.85, 0.25 + (max_score / len(emotion_indicators[top_emotion])) * 0.6)
                return {
                    "emotion": top_emotion.value,
                    "confidence": confidence
                }
            
            # Fallback to rule-based if spaCy didn't detect strong emotions
            return self._rule_based_emotion_detection(text)
            
        except Exception as e:
            logger.warning(f"Error in spaCy emotion detection: {e}")
            return self._rule_based_emotion_detection(text)
    
    def _rule_based_emotion_detection(self, text: str) -> Dict[str, Any]:
        """Rule-based fallback for emotion detection."""
        text_lower = text.lower()
        
        # Define emotion keywords with weights
        emotion_weights = {
            EmotionType.ANXIETY: ["worried", "anxious", "nervous", "stressed", "uneasy", "apprehensive", "panic", "panic attack"],
            EmotionType.FRUSTRATION: ["frustrated", "annoyed", "irritated", "exasperated", "impatient", "aggravated"],
            EmotionType.WORRY: ["worry", "worried", "concerned", "troubled", "disturbed", "brooding"],
            EmotionType.SADNESS: ["sad", "unhappy", "depressed", "down", "blue", "melancholy", "tearful", "weepy"],
            EmotionType.ANGER: ["angry", "mad", "furious", "irate", "livid", "outraged", "hostile", "resentful"],
            EmotionType.GUILT: ["guilty", "guilt", "remorseful", "regretful", "contrite", "self-blame"],
            EmotionType.HOPE: ["hopeful", "hope", "optimistic", "encouraged", "positive", "looking forward"],
            EmotionType.RELIEF: ["relieved", "relief", "better", "improved", "easier", "less painful"],
            EmotionType.GRATITUDE: ["grateful", "thankful", "thanks", "appreciative", "thank you", "thanks a lot"],
            EmotionType.LOVE_AFFECTION: ["love", "love you", "care about", "fond", "affectionate", "devoted"],
            EmotionType.EMPATHY: ["understand", "feel for", "sympathize", "compassionate", "commiserate"],
            EmotionType.OVERWHELMED: ["overwhelmed", "too much", "can't handle", "swamped", "buried", "at my limit"],
            EmotionType.EXHAUSTION: ["exhausted", "tired", "worn out", "drained", "fatigued", "weary", "burnt out"]
        }
        
        emotion_scores = {}
        for emotion_type, keywords in emotion_weights.items():
            score = sum(1 for keyword in keywords if keyword in text_lower)
            emotion_scores[emotion_type] = score
        
        # Find emotion with highest score
        if emotion_scores and max(emotion_scores.values()) > 0:
            top_emotion = max(emotion_scores, key=emotion_scores.get)
            max_score = emotion_scores[top_emotion]
            # Calculate confidence based on match strength
            confidence = min(0.8, 0.2 + (max_score / 3) * 0.6)  # Assume 3+ matches = high confidence
            return {
                "emotion": top_emotion.value,
                "confidence": confidence
            }
        
        # Default to neutral
        return {
            "emotion": EmotionType.NEUTRAL.value,
            "confidence": 0.3
        }
    
    def _analyze_sentiment(self, text: str) -> Dict[str, Any]:
        """Analyze sentiment of text."""
        # Try NLTK first
        if self.sia:
            try:
                scores = self.sia.polarity_scores(text)
                # Convert to our format
                compound = scores['compound']
                if compound >= 0.05:
                    label = "positive"
                elif compound <= -0.05:
                    label = "negative"
                else:
                    label = "neutral"
                
                return {
                    "polarity": compound,  # -1 to 1
                    "subjectivity": 0.5,  # NLTK doesn't provide subjectivity, use placeholder
                    "label": label,
                    "scores": {
                        "positive": scores['pos'],
                        "negative": scores['neg'],
                        "neutral": scores['neu']
                    }
                }
            except Exception as e:
                logger.warning(f"Error in NLTK sentiment analysis: {e}")
        
        # Try TextBlob as fallback
        if TEXTBLOB_AVAILABLE:
            try:
                blob = TextBlob(text)
                polarity = blob.sentiment.polarity  # -1 to 1
                subjectivity = blob.sentiment.subjectivity  # 0 to 1
                
                if polarity > 0.1:
                    label = "positive"
                elif polarity < -0.1:
                    label = "negative"
                else:
                    label = "neutral"
                
                return {
                    "polarity": polarity,
                    "subjectivity": subjectivity,
                    "label": label
                }
            except Exception as e:
                logger.warning(f"Error in TextBlob sentiment analysis: {e}")
        
        # Rule-based fallback
        return self._rule_based_sentiment(text)
    
    def _rule_based_sentiment(self, text: str) -> Dict[str, Any]:
        """Rule-based sentiment analysis fallback."""
        text_lower = text.lower()
        
        positive_words = [
            "good", "great", "excellent", "wonderful", "fantastic", "amazing", 
            "happy", "pleased", "glad", "joyful", "delighted", "content", 
            "satisfied", "thankful", "grateful", "appreciative", "love", "love you",
            "best", "better", "improved", "healing", "recovering", "hope", "hopeful",
            "positive", "nice", "pleasant", "okay", "alright", "fine"
        ]
        
        negative_words = [
            "bad", "terrible", "awful", "horrible", "worst", "hate", "hate you",
            "angry", "mad", "furious", "upset", "sad", "unhappy", "depressed", 
            "miserable", "disappointed", "frustrated", "frustration", "annoyed",
            "worried", "worry", "anxious", "anxiety", "scared", "afraid", "fear",
            "pain", "hurt", "suffering", "difficult", "hard", "trouble", "problem",
            "issue", "complaint", "complain", "not working", "doesn't help",
            "ineffective", "useless", "worse", "worsening", "declining", "deteriorating"
        ]
        
        pos_count = sum(1 for word in positive_words if word in text_lower)
        neg_count = sum(1 for word in negative_words if word in text_lower)
        
        total = pos_count + neg_count
        if total == 0:
            polarity = 0.0
            label = "neutral"
        else:
            polarity = (pos_count - neg_count) / (pos_count + neg_count)
            if polarity > 0.1:
                label = "positive"
            elif polarity < -0.1:
                label = "negative"
            else:
                label = "neutral"
        
        # Estimate subjectivity based on emotional word presence
        emotional_words = set(positive_words + negative_words)
        emotional_count = sum(1 for word in emotional_words if word in text_lower)
        total_words = len(text_lower.split())
        subjectivity = min(0.9, emotional_count / max(total_words, 1) * 3) if total_words > 0 else 0.5
        
        return {
            "polarity": max(-1.0, min(1.0, polarity)),
            "subjectivity": max(0.0, min(1.0, subjectivity)),
            "label": label
        }
    
    def _extract_keywords(self, text: str) -> List[str]:
        """Extract important keywords from text."""
        # Simple keyword extraction based on noun phrases and important terms
        keywords = []
        
        if self.nlp:
            try:
                doc = self.nlp(text)
                # Extract noun phrases and important adjectives/adverbs
                for chunk in doc.noun_chunks:
                    if len(chunk.text.strip()) > 2:  # Avoid very short chunks
                        keywords.append(chunk.text.strip().lower())
                
                # Add important named entities
                for ent in doc.ents:
                    if ent.label_ in ["PERSON", "ORG", "PRODUCT", "EVENT", "WORK_OF_ART"]:
                        keywords.append(ent.text.lower())
            except Exception as e:
                logger.warning(f"Could not extract keywords with spaCy: {e}")
        
        # Fallback: extract words that are not stop words
        if not keywords:
            # Common stop words to exclude
            stop_words = {
                "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", 
                "of", "with", "by", "is", "are", "was", "were", "be", "been", "being",
                "have", "has", "had", "do", "does", "did", "will", "would", "could",
                "should", "may", "might", "must", "can", "this", "that", "these", "those",
                "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us", "them",
                "my", "your", "his", "her", "its", "our", "their", "mine", "yours", "his", "hers",
                "ours", "theirs", "what", "which", "who", "whom", "whose", "where", "when", "why",
                "how", "not", "no", "yes", "up", "down", "out", "off", "over", "under", "again",
                "further", "then", "once", "here", "there", "when", "where", "why", "how", "all",
                "any", "both", "each", "few", "more", "most", "other", "some", "such", "only", "own",
                "same", "so", "than", "too", "very", "s", "t", "can", "will", "just", "don", "should",
                "now", "d", "ll", "m", "o", "re", "ve", "y", "ain", "aren", "couldn", "didn", "doesn",
                "hadn", "hasn", "haven", "isn", "ma", "mightn", "mustn", "needn", "shan", "shouldn",
                "wasn", "weren", "won", "wouldn"
            }
            
            words = re.findall(r'\b[a-zA-Z]{3,}\b', text_lower)
            keywords = [word for word in words if word not in stop_words][:10]  # Limit to top 10
        
        # Remove duplicates while preserving order
        seen = set()
        unique_keywords = []
        for keyword in keywords:
            if keyword not in seen:
                seen.add(keyword)
                unique_keywords.append(keyword)
        
        return unique_keywords[:10]  # Return top 10 keywords
    
    def _generate_analysis_details(self, 
                                 text: str, 
                                 intent_result: Dict[str, Any], 
                                 emotion_result: Dict[str, Any], 
                                 sentiment_result: Dict[str, Any],
                                 entities: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate detailed analysis information."""
        details = {
            "text_length": len(text),
            "word_count": len(text.split()),
            "sentence_count": len([s for s in text.split('.') if s.strip()]),
            "has_question": "?" in text,
            "has_exclamation": "!" in text,
            "starts_with_question_word": text.lower().strip().startswith((
                "what", "how", "why", "when", "where", "who", "which"
            )),
            "contains_please": "please" in text.lower(),
            "thank_you_present": any(phrase in text.lower() for phrase in 
                                   ["thank", "thanks", "thankful", "grateful"]),
            "apology_present": any(phrase in text.lower() for phrase in 
                                 ["sorry", "apologize", "apology", "my bad"]),
            "urgency_indicators": [],
            "certainty_level": "moderate"
        }
        
        # Check for urgency indicators
        urgency_indicators = [
            "emergency", "urgent", "asap", "immediately", "right now", 
            "right away", "stat", "critical", "serious", "severe", 
            "badly", "terribly", "extremely", "very"
        ]
        for indicator in urgency_indicators:
            if indicator in text.lower():
                details["urgency_indicators"].append(indicator)
        
        # Determine certainty level based on linguistic markers
        certainty_phrases = [
            "i know", "i'm sure", "i am sure", "definitely", "certainly", 
            "absolutely", "without doubt", "no doubt"
        ]
        uncertainty_phrases = [
            "i think", "i believe", "i guess", "maybe", "perhaps", "possibly",
            "might be", "could be", "i suppose", "i feel like", "it seems",
            "appears to be", "looks like", "sort of", "kind of"
        ]
        
        certainty_count = sum(1 for phrase in certainty_phrases if phrase in text.lower())
        uncertainty_count = sum(1 for phrase in uncertainty_phrases if phrase in text.lower())
        
        if certainty_count > uncertainty_count:
            details["certainty_level"] = "high"
        elif uncertainty_count > certainty_count:
            details["certainty_level"] = "low"
        else:
            details["certainty_level"] = "moderate"
        
        return details
    
    def _get_default_result(self) -> Dict[str, Any]:
        """Return default result for empty or invalid input."""
        return {
            "text": "",
            "cleaned_text": "",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "intent": IntentType.UNCLEAR.value,
            "intent_confidence": 0.0,
            "emotion": EmotionType.NEUTRAL.value,
            "emotion_confidence": 0.0,
            "sentiment": {
                "polarity": 0.0,
                "subjectivity": 0.0,
                "label": "neutral"
            },
            "detailed_scores": {},
            "keywords": [],
            "entities": [],
            "analysis_details": {
                "text_length": 0,
                "word_count": 0,
                "sentence_count": 0,
                "has_question": False,
                "has_exclamation": False,
                "starts_with_question_word": False,
                "contains_please": False,
                "thank_you_present": False,
                "apology_present": False,
                "urgency_indicators": [],
                "certainty_level": "low"
            }
        }

# Global instance for reuse
_caregiver_intent_service = None

def get_caregiver_intent_service() -> CaregiverIntentService:
    """Get or create the global Caregiver Intent service instance."""
    global _caregiver_intent_service
    if _caregiver_intent_service is None:
        _caregiver_intent_service = CaregiverIntentService()
    return _caregiver_intent_service

def understand_caregiver_intent(text: str) -> Dict[str, Any]:
    """
    Convenience function to understand caregiver intent and emotional state.
    
    Args:
        text: Input text from caregiver
        
    Returns:
        Dictionary containing intent, emotion, sentiment analysis, and details
    """
    service = get_caregiver_intent_service()
    return service.understand_intent_and_emotion(text)