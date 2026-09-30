import type {
  CommunityPost,
  CommunityPostStatus,
  CreatePostInput,
  CreateReactionInput,
  CommunityReaction,
  CommunityAuthorType,
  CommunityVisibility,
  CommunityCareStage,
  CommunityTag,
  ReactionType,
} from "./types";
import {
  DEFAULT_RELEVANCE_TTL_HOURS,
  COMMUNITY_CARE_STAGES,
  COMMUNITY_TAGS,
  COMMUNITY_VISIBILITY,
  COMMUNITY_AUTHOR_TYPES,
} from "./contract-constants";

const posts = new Map<string, CommunityPost>();
const reactions = new Map<string, CommunityReaction>();
const caregiverPostIndex = new Map<string, string[]>();
const postReactionIndex = new Map<string, string[]>();

export function createPostId(): string {
  return `cp_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
}

export function createReactionId(): string {
  return `cr_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
}

function avatarSeed(label: string): string {
  return label.split(" ").map((w) => w[0]).join("").toLowerCase().slice(0, 2);
}

function isValidEnum<T extends readonly string[]>(
  arr: T,
  val: unknown,
): val is T[number] {
  return typeof val === "string" && (arr as readonly string[]).includes(val);
}

export function createPost(input: CreatePostInput): CommunityPost {
  const now = new Date().toISOString();
  const expiresAt =
    input.title === null || input.body.trim() === ""
      ? now
      : new Date(Date.now() + DEFAULT_RELEVANCE_TTL_HOURS * 3600_000).toISOString();

  const post: CommunityPost = {
    id: createPostId(),
    caregiver_id: input.caregiver_id,
    author_type: input.author_type ?? "peer_caregiver",
    author_label: input.author_label ?? "Anonymous caregiver",
    author_avatar_seed: avatarSeed(input.author_label ?? "Anonymous caregiver"),
    title: input.title ?? null,
    body: input.body.trim(),
    media: input.media ?? [],
    visibility: input.visibility ?? "public",
    care_stage: input.care_stage ?? "unspecified",
    tags: input.tags ?? [],
    status: "pending_moderation",
    moderation_reason: null,
    relevance_expires_at: expiresAt,
    post_type: input.post_type ?? "experience",
    question_status: input.post_type === "question" ? ("open" as const) : null,
    helpful_marks: 0,
    created_at: now,
    updated_at: null,
    provenance_source: `community:${input.author_type ?? "peer_caregiver"}`,
  };

  posts.set(post.id, post);
  indexPost(post);

  return post;
}

function indexPost(post: CommunityPost): void {
  const ids = caregiverPostIndex.get(post.caregiver_id) ?? [];
  ids.unshift(post.id);
  caregiverPostIndex.set(post.caregiver_id, ids);
}

export function getPost(id: string): CommunityPost | undefined {
  return posts.get(id);
}

export function listPostsForCaregiver(caregiverId: string): CommunityPost[] {
  const ids = caregiverPostIndex.get(caregiverId) ?? [];
  return ids
    .map((id) => posts.get(id))
    .filter((p): p is CommunityPost => p !== undefined)
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
}

export function listPublicPosts(opts?: {
  tags?: CommunityTag[];
  care_stages?: CommunityCareStage[];
  limit?: number;
}): CommunityPost[] {
  const all = [...posts.values()]
    .filter((p) => p.visibility === "public" && p.status === "approved")
    .sort((a, b) => b.created_at.localeCompare(a.created_at));

  let filtered = all;

  if (opts?.tags && opts.tags.length > 0) {
    filtered = filtered.filter((p) => opts.tags!.some((t) => p.tags.includes(t)));
  }

  if (opts?.care_stages && opts.care_stages.length > 0) {
    filtered = filtered.filter((p) => opts.care_stages!.includes(p.care_stage));
  }

  const limit = opts?.limit ?? 50;
  return filtered.slice(0, limit);
}

export function listPendingPosts(): CommunityPost[] {
  return [...posts.values()]
    .filter((p) => p.status === "pending_moderation" || p.status === "flagged")
    .sort((a, b) => a.created_at.localeCompare(b.created_at));
}

export function listUnansweredQuestions(limit: number = 20): CommunityPost[] {
  return [...posts.values()]
    .filter(
      (p) =>
        p.post_type === "question" &&
        p.question_status === "open" &&
        p.status === "approved",
    )
    .sort((a, b) => a.created_at.localeCompare(b.created_at))
    .slice(0, limit);
}

export function markPostHelpful(postId: string): CommunityPost | undefined {
  const post = posts.get(postId);
  if (!post) return undefined;
  const updated = { ...post, helpful_marks: post.helpful_marks + 1 };
  posts.set(postId, updated);
  return updated;
}

export function countUnansweredQuestions(): number {
  return [...posts.values()].filter(
    (p) => p.post_type === "question" && p.question_status === "open",
  ).length;
}

export function countDistressSignals(): number {
  return [...posts.values()].filter(
    (p) => p.moderation_reason === "distress_signal",
  ).length;
}

export function listDistressPosts(sinceHours: number = 24): CommunityPost[] {
  const cutoff = new Date(Date.now() - sinceHours * 3_600_000).toISOString();
  return [...posts.values()]
    .filter(
      (p) =>
        p.moderation_reason === "distress_signal" && p.created_at >= cutoff,
    )
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
}

export function updatePostModeration(
  id: string,
  patch: {
    status?: CommunityPostStatus;
    moderation_reason?: string | null;
    care_stage?: CommunityCareStage;
    tags?: CommunityTag[];
  },
): CommunityPost | undefined {
  const post = posts.get(id);
  if (!post) return undefined;

  const updated: CommunityPost = {
    ...post,
    ...patch,
    updated_at: new Date().toISOString(),
    provenance_source: patch.status
      ? `community:moderated(${patch.status})`
      : post.provenance_source,
  };

  posts.set(id, updated);
  return updated;
}

export function updatePost(
  id: string,
  patch: Partial<
    Pick<CommunityPost, "status" | "visibility" | "care_stage" | "tags" | "title" | "body">
  >,
): CommunityPost | undefined {
  const post = posts.get(id);
  if (!post) return undefined;
  const updated: CommunityPost = { ...post, ...patch, updated_at: new Date().toISOString() };
  posts.set(id, updated);
  return updated;
}

export function archiveExpiredPosts(referenceTime: Date = new Date()): void {
  const now = referenceTime.toISOString();
  for (const post of posts.values()) {
    if (
      post.relevance_expires_at &&
      post.relevance_expires_at <= now &&
      post.status !== "archived"
    ) {
      posts.set(post.id, {
        ...post,
        status: "archived",
        updated_at: now,
        provenance_source: "community:auto-archive",
      });
    }
  }
}

export function createReaction(input: CreateReactionInput): CommunityReaction {
  const reaction: CommunityReaction = {
    id: createReactionId(),
    post_id: input.post_id,
    caregiver_id: input.caregiver_id,
    author_type: input.author_type ?? "peer_caregiver",
    reaction_type: input.reaction_type,
    created_at: new Date().toISOString(),
  };

  reactions.set(reaction.id, reaction);
  const ids = postReactionIndex.get(input.post_id) ?? [];
  ids.push(reaction.id);
  postReactionIndex.set(input.post_id, ids);

  return reaction;
}

export function getReactionsForPost(postId: string): CommunityReaction[] {
  const ids = postReactionIndex.get(postId) ?? [];
  return ids
    .map((id) => reactions.get(id))
    .filter((r): r is CommunityReaction => r !== undefined)
    .sort((a, b) => a.created_at.localeCompare(b.created_at));
}

export function getReactionCounts(postId: string): Record<ReactionType, number> {
  const counts = {
    like: 0,
    thank_you: 0,
    support: 0,
    caregiver_answer: 0,
    caregiver_helpful: 0,
  } as Record<ReactionType, number>;

  const ids = postReactionIndex.get(postId) ?? [];
  for (const id of ids) {
    const reaction = reactions.get(id);
    if (reaction) {
      counts[reaction.reaction_type] = (counts[reaction.reaction_type] ?? 0) + 1;
    }
  }
  return counts;
}

export function getUserReaction(
  postId: string,
  caregiverId: string,
): ReactionType | null {
  const ids = postReactionIndex.get(postId) ?? [];
  for (const id of ids) {
    const reaction = reactions.get(id);
    if (reaction && reaction.caregiver_id === caregiverId) {
      return reaction.reaction_type;
    }
  }
  return null;
}

export function countCommentsForPost(_postId: string): number {
  return 0;
}

export function seedCommunityPosts(caregiverId: string = "default_caregiver"): CommunityPost[] {
  resetCommunityStore();

  const samples: CreatePostInput[] = [
    {
      caregiver_id: caregiverId,
      author_type: "peer_caregiver",
      author_label: "Sarah M.",
      title: "Managing sundowning evenings",
      body: "Dad starts getting agitated around 5pm — pacing, asking where Mom is. The thing that finally helps is dimming lights 30 minutes before, playing his old jazz playlist, and quietly redirecting with a simple task like folding towels. It's not perfect, but the episodes dropped from daily to 2-3 times a week.",
      visibility: "public",
      care_stage: "moderate",
      tags: ["sundowning", "behavior", "activities", "sleep"],
    },
    {
      caregiver_id: caregiverId,
      author_type: "peer_caregiver",
      author_label: "Robert & Maria",
      title: "Medication adherence hack",
      body: "Mom refused her evening meds for three days straight — we tried pill organizers, reminders, everything. Finally worked: mixing the small pill in a tiny bit of applesauce she loves. Not medical advice, just sharing what got us past this particular hurdle.",
      visibility: "public",
      care_stage: "moderate",
      tags: ["medication", "behavior"],
    },
    {
      caregiver_id: caregiverId,
      author_type: "solenos_verified",
      author_label: "SolenOS",
      title: "Before your next appointment",
      body: "Based on your care record, here are the top items to discuss with the neurologist: (1) recent weight trend — noted 3 lb loss over 2 weeks, (2) wandering episodes increased to 4x this week, (3) new sleep pattern — waking 2-3 hours earlier. Other dementia caregivers flagged tracking daily weight as important before visits.",
      visibility: "public",
      care_stage: "moderate",
      tags: ["medication", "wandering", "nutrition", "appointments"],
    },
    {
      caregiver_id: caregiverId,
      author_type: "peer_caregiver",
      author_label: "Elena R.",
      title: "Safe home setup — what worked for wandering",
      body: "Installed door alarms and removed keys from his pocket (he kept trying to leave). Also put a motion sensor nightlight in the hallway. The alarms are loud but they give me enough time to redirect before he reaches the door. Still not foolproof, but safer.",
      visibility: "public",
      care_stage: "late",
      tags: ["wandering", "safety", "mobility"],
    },
    {
      caregiver_id: caregiverId,
      author_type: "peer_caregiver",
      author_label: "David K.",
      title: "Calming techniques for agitation",
      body: "When Dad gets combative or confused, I've learned NOT to argue or correct him. Instead: validate his emotion ('I can see you're upset'), offer physical comfort (hand on shoulder), and redirect to a familiar activity (looking at photo albums). It doesn't eliminate agitation but reduces its intensity.",
      visibility: "public",
      care_stage: "moderate",
      tags: ["behavior", "communication", "activities"],
    },
  ];

  const created: CommunityPost[] = [];
  for (const sample of samples) {
    const post = createPost(sample);
    updatePostModeration(post.id, {
      status: "approved",
      moderation_reason: null,
    });
    created.push(post);
  }

  return created;
}

export function resetCommunityStore(): void {
  posts.clear();
  reactions.clear();
  caregiverPostIndex.clear();
  postReactionIndex.clear();
}

export {
  isValidEnum,
  COMMUNITY_AUTHOR_TYPES as _authorTypes,
  COMMUNITY_VISIBILITY as _visibility,
  COMMUNITY_CARE_STAGES as _careStages,
  COMMUNITY_TAGS as _tags,
};
