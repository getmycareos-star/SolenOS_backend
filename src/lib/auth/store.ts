import { Pool } from "pg";

const pool = new Pool({ connectionString: process.env.DATABASE_URL });

export interface CaregiverRecord {
  id: string;
  email: string;
  password_hash: string;
  display_name: string;
  care_recipient_ids: string[];
  role: "caregiver" | "admin";
  created_at: Date;
  updated_at: Date;
  last_login_at: Date | null;
}

export interface AuthContext {
  userId: string;
  role?: string;
}

async function withRls<T>(
  authCtx: AuthContext | null,
  fn: (client: Pool) => Promise<T>,
): Promise<T> {
  if (!authCtx) {
    return fn(pool);
  }

  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    await client.query(
      `SET LOCAL request.jwt.claims TO $1`,
      [JSON.stringify({ sub: authCtx.userId, role: authCtx.role ?? "authenticated" })],
    );
    const result = await fn(client as unknown as Pool);
    await client.query("COMMIT");
    return result;
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  } finally {
    client.release();
  }
}

export async function createCaregiver(
  data: {
    id: string;
    email: string;
    password_hash: string;
    display_name: string;
    care_recipient_ids: string[];
    role?: "caregiver" | "admin";
  },
  authCtx: AuthContext | null = null,
): Promise<CaregiverRecord> {
  return withRls(authCtx, async (client) => {
    const result = await client.query<CaregiverRecord>(
      `INSERT INTO caregivers (id, email, password_hash, display_name, care_recipient_ids, role)
       VALUES ($1, $2, $3, $4, $5::jsonb, $6)
       RETURNING *`,
      [data.id, data.email, data.password_hash, data.display_name, JSON.stringify(data.care_recipient_ids), data.role ?? "caregiver"],
    );
    return result.rows[0];
  });
}

export async function findCaregiverByEmail(
  email: string,
  authCtx: AuthContext | null = null,
): Promise<CaregiverRecord | null> {
  return withRls(authCtx, async (client) => {
    const result = await client.query<CaregiverRecord>(
      `SELECT * FROM caregivers WHERE email = $1`,
      [email],
    );
    return result.rows[0] ?? null;
  });
}

export async function findCaregiverById(
  id: string,
  authCtx: AuthContext | null = null,
): Promise<CaregiverRecord | null> {
  return withRls(authCtx, async (client) => {
    const result = await client.query<CaregiverRecord>(
      `SELECT * FROM caregivers WHERE id = $1`,
      [id],
    );
    return result.rows[0] ?? null;
  });
}

export async function updateLastLogin(
  id: string,
  authCtx: AuthContext | null = null,
): Promise<void> {
  return withRls(authCtx, async (client) => {
    await client.query(
      `UPDATE caregivers SET last_login_at = NOW(), updated_at = NOW() WHERE id = $1`,
      [id],
    );
  });
}

export async function updateCaregiverCareRecipients(
  id: string,
  careRecipientIds: string[],
  authCtx: AuthContext | null = null,
): Promise<void> {
  return withRls(authCtx, async (client) => {
    await client.query(
      `UPDATE caregivers SET care_recipient_ids = $2::jsonb, updated_at = NOW() WHERE id = $1`,
      [id, JSON.stringify(careRecipientIds)],
    );
  });
}

export async function linkCareRecipientToCaregiver(
  caregiverId: string,
  careRecipientId: string,
  authCtx: AuthContext | null = null,
): Promise<void> {
  return withRls(authCtx, async (client) => {
    await client.query(
      `UPDATE caregivers
       SET care_recipient_ids = (
         SELECT jsonb_agg(DISTINCT elem)
         FROM (
           SELECT jsonb_array_elements(care_recipient_ids) AS elem
           UNION
           SELECT to_jsonb($2::text)
         ) sub
       ),
       updated_at = NOW()
       WHERE id = $1`,
      [caregiverId, careRecipientId],
    );
  });
}
