# Stage 21 decision — Instagram and Facebook directly through Meta: not adopted (2026-10-22)

**Decision:** Instagram and Facebook stay on Buffer (Stage 20), like TikTok. The user's rule:
"if Meta needs app review for API posting, leave it like TikTok". Meta effectively does.

## What was checked (official sources, read 2026-10-22)

| Question | Finding | Source |
|---|---|---|
| Local upload without hosting? | Possible: Facebook Reels (`/{page-id}/video_reels` + `rupload.facebook.com`) and Instagram Reels (`upload_type=resumable`), but Instagram's resumable upload only with **Instagram API with Facebook Login** (Page token). | Reels publishing; Instagram content publishing |
| Sign-in for a desktop app | Business apps use Facebook Login for Business (`config_id`); the code exchange and the 60-day long-lived token need the **App Secret**. Page tokens from a long-lived user token don't expire. Instagram permissions are not on the non-expiring list (≈90-day re-consent). | FB Login for Business; manual flow; long-lived tokens; auth vs data access |
| Without App Review? | **Development mode:** "Any data generated while an app is in Development mode, such as test posts, can only be seen by role users." Posts would not be public. | App modes |
| Going public | Meta: "only switch the app to Live mode after you have completed App Review". Facebook Login for Business apps need **Advanced Access to `public_profile`** before going live; Advanced Access requires **Business Verification**. | App modes; FB Login for Business; access levels |

Conclusion: public posting through the user's own Meta app needs App Review and Business
Verification in practice, so the Buffer route (no review, public posts, posts even while the Mac
sleeps) remains the implementation. No Stage 21 code was kept; the Stage 20 code and the live
library are unchanged.

If this is revisited (for example after obtaining Business Verification), the research above and
the Stage 20 records/automation code are the starting point.
