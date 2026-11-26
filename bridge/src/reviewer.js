// The directories' and stores' reviewers (Claude, ChatGPT, Microsoft) sign in as one account with a fixed code, kept
// in the REVIEWER_CODE secret: their test accounts may not need an emailed code. No email is sent for it, wrong codes
// count as for anyone, and without the secret the address is an ordinary one. Its credits top themselves up
// (credits.js), so testers can try what the paid plans give (more credits for Claude's checks) without paying.
export const reviewerEmail = (env) => String(env.REVIEWER_EMAIL || "reviewer@citrello.app").toLowerCase();
export const reviewerCode = (env) => String(env.REVIEWER_CODE || "").replace(/\D/g, "");
export const isReviewer = (env, email) => reviewerCode(env).length >= 8 && String(email || "").toLowerCase() === reviewerEmail(env);
export const REVIEWER_CREDITS = 4000, REVIEWER_TOP_UP_BELOW = 1000;
