export {
  COMMUNITY_AUTHOR_TYPES,
  COMMUNITY_VISIBILITY,
  COMMUNITY_POST_STATUSES,
  COMMUNITY_POST_TYPES,
  COMMUNITY_CARE_STAGES,
  COMMUNITY_TAGS,
  REACTION_TYPES,
  COMMUNITY_IDENTITY,
  DEFAULT_RELEVANCE_TTL_HOURS,
  COMMUNITY_STORE_SCHEMA,
} from "./contract-constants";

export type {
  CommunityPost,
  CommunityReaction,
  CreatePostInput,
  CreateReactionInput,
  CommunityPostSummary,
  MediaAttachment,
  PostType,
  QuestionStatus,
} from "./types";

export type {
  CommunityAuthorType,
  CommunityVisibility,
  CommunityCareStage,
  CommunityTag,
  CommunityPostStatus,
  ReactionType,
} from "./types";

export {
  createPost,
  getPost,
  listPostsForCaregiver,
  listPublicPosts,
  listPendingPosts,
  listUnansweredQuestions,
  updatePostModeration,
  updatePost,
  archiveExpiredPosts,
  createReaction,
  getReactionsForPost,
  getReactionCounts,
  getUserReaction,
  countCommentsForPost,
  countUnansweredQuestions,
  countDistressSignals,
  listDistressPosts,
  markPostHelpful,
  resetCommunityStore,
  seedCommunityPosts,
  createPostId,
  createReactionId,
} from "./store";

export {
  moderateContent,
  inferCareStage,
  mergeTags,
  isStageValid,
  type ModerationResult,
} from "./moderation";

export {
  COMMUNITY_EXPERTISE_DOMAINS,
  CREDENTIAL_TYPES,
  PROFESSIONAL_TYPES,
  PROFESSIONAL_EXPERTISE_AREAS,
  PROFESSIONAL_TITLE_MAP,
} from "./professional-constants";

export type {
  ProfessionalProfile,
  ProfessionalProfileSummary,
  SubmitVerificationInput,
  ProfessionalExpertise,
  CredentialSubmission,
  ExpertiseDomain,
  CredentialType,
  ProfessionalType,
  ProfessionalExpertiseArea,
} from "./professional-types";

export {
  createProfessionalProfile,
  approveVerification,
  getProfessionalProfile,
  getProfessionalByCaregiverId,
  listVerifiedProfessionals,
  listPendingVerifications,
  updateProfessionalImpact,
  findProfessionalsByExpertise,
  toSummary as professionalSummary,
  resetProfessionalStore,
} from "./professional-store";

export {
  computeCommunityHealth,
  type CommunityHealthMetrics,
  type DistressSignalSummary,
  type TagDistribution,
  type StageDistribution,
} from "./metrics";

export {
  findSimilarExperiences,
  extractObservationKeywords,
  type SimilarExperiencesResult,
  type ExperienceMatch,
} from "./experience-matching";

export {
  computeReputation,
  recordReputationSignal,
  deriveBadgeLabel,
  resetReputationStore,
  getReputationForCaregivers,
  type ReputationSignal,
  type CaregiverReputation,
  type ReputationWeight,
  REPUTATION_WEIGHTS,
  TRUSTED_CAREGIVER_THRESHOLD,
} from "./reputation";

export {
  createMilestone,
  checkMilestoneThresholds,
  type Milestone,
  type MilestoneType,
  MILESTONE_DEFS,
} from "./milestones";

export {
  computeHelpRequests,
  getProfessionalHelpFeed,
  type HelpRequest,
  type ProfessionalHelpFeed,
} from "./help-requests";

export {
  computeTeamDashboard,
  computeStateOfDementiaCare,
  type TeamDashboard,
  type StateReport,
  type CaregiverNeedingAttention,
  type PostNeedsReview,
  type TrendingTag,
} from "./team-dashboard";
