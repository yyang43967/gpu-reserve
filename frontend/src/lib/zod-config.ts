import { z } from "zod";

// Zod compiles object schemas with `new Function` when it can, and on first use
// it probes for that by constructing one inside a try/catch. The site's Content
// Security Policy has no 'unsafe-eval', so the probe fails harmlessly but still
// raises a policy violation report on every page load. Jitless mode skips the
// probe and always uses the interpreted parser, which is fast enough for forms.
// Import this module before anything that parses with Zod.
z.config({ jitless: true });
