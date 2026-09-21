# KRATOS'26 — API Endpoints Reference

**Base path:** `/api/v1`

This reference is aligned to the finalized KRATOS'26 database model and the agreed registration flows.

---

# 1. Authentication

### `POST /auth/google`
Authenticate/sign in with Google OAuth.

**Does**
- Authenticate the Google account.
- Create the `USERS` row if required.
- Create/retrieve the reusable `PROFILES` row.
- Establish the authenticated session.

**Access:** Public.

### `GET /auth/me`
Return the authenticated user's account, profile, and applicable admin information.

**Access:** Authenticated user.

### `POST /auth/logout`
Invalidate the current authenticated session.

**Access:** Authenticated user.

---

# 2. User Profile

### `GET /users/me/profile`
Return the current user's reusable participant profile.

**Does**
- Return name, phone, college, department, year and contact email.
- Allow the frontend to prefill future registrations.

**Access:** Authenticated user.

### `PATCH /users/me/profile`
Update the current user's reusable participant profile.

**Access:** Authenticated user.

**Note:** The Google authentication email in `USERS.email` remains the authentication identity. An editable `PROFILES.contact_email` is separate.

---

# 3. Events

### `GET /events`
List available events.

**Does**
- Return event catalogue information.
- Return registration availability/status.
- Return basic pricing and team information.

**Access:** Public.

### `GET /events/{event_id}`
Return complete event configuration needed by the registration frontend.

**Includes**
- Event metadata.
- Registration open/close times.
- Team min/max size.
- Individual registration availability.
- Pricing model.
- Capacity type.
- Member registration mode.
- Dynamic custom fields.
- QR requirement.
- WhatsApp group link.

**Access:** Public.

---

# 4. Registration

### `POST /events/{event_id}/registrations`
Create a solo registration or initialize a team registration.

**Does**
- Validate event status, registration dates and capacity.
- Validate eligibility and duplicate event membership.
- Create the appropriate registration/team/payment state.

**Access:** Authenticated user.

### `GET /registrations/{registration_id}`
Return a registration and its current payment/team/confirmation state.

**Access:** Registration owner, applicable team leader/member, or authorized admin.

### `GET /users/me/registrations`
Return all registrations belonging to the authenticated user.

**Access:** Authenticated user.

### `GET /registrations/{registration_id}/receipt`
Retrieve/download the receipt associated with the registration/payment.

**Access:** Registration owner or authorized admin.

### `GET /registrations/{registration_id}/qr`
Return the QR for a confirmed participant.

**QR ownership**
- Team participant → `QR_CODES.team_member_id`
- Solo participant → `QR_CODES.registration_id`

**Access:** Registration owner, applicable team member/leader, or authorized admin.

---

# 5. Teams

### `POST /events/{event_id}/teams`
Create a team.

**Does**
- Create the team.
- Set the authenticated user as leader.
- Create the leader's `TEAM_MEMBERS` row.
- Validate team rules.
- Prepare the team for payment.

**Access:** Authenticated user.

### `GET /teams/{team_id}`
Return team details, leader, status and summary membership information.

**Access:** Team member/leader or authorized admin.

### `GET /teams/{team_id}/members`
Return team members and their statuses.

**Access:** Team member/leader or authorized admin.

### `PATCH /teams/{team_id}`
Update permitted team information such as team name.

**Access:** Team leader or authorized admin.

### `POST /teams/{team_id}/invitations`
Create/regenerate a reusable invitation link.

**Does**
- Generate a secure invitation code.
- Keep it reusable/non-expiring until explicitly revoked.

**Access:** Team leader or authorized admin.

---

# 6. Team Member Management

### `POST /team-invitations/{invite_code}/join`
Join a team through an invitation.

**Does**
- Validate invitation.
- Validate event membership uniqueness.
- Atomically check team capacity.
- Prevent duplicate joins.

**Pricing behavior (locked)**
- Team members **never pay**.
- After the team is `PAID` (leader payment verified), joiners become `ACTIVE` with no Razorpay step and no new `PAYMENTS` / `REGISTRATIONS` row.

**Access:** Authenticated user.

### `POST /teams/{team_id}/members/{member_id}/leave`
Allow a member to leave their current team.

**Does**
- Change membership status to `LEFT`.
- Preserve the membership history.
- Apply any event-specific rules for leaving after payment.

**Access:** The member represented by `member_id`.

### `POST /teams/{team_id}/members/{member_id}/remove`
Remove a member from a team.

**Does**
- Change membership status to `REMOVED`.
- Preserve history.
- Prevent further team participation through that membership.

**Access:** Team leader or authorized admin.

### `GET /team-invitations/{invite_code}`
Validate an invitation and return:
- Event.
- Team.
- Leader.
- Current capacity/status.

**Access:** Public.

---

# 7. Optional Team Real-Time Updates

### `WebSocket /teams/{team_id}`
Optional real-time team updates.

**Can notify**
- Member joined.
- Member left/was removed.
- Team status changed.
- Team became complete.

Normal API refetching can be used if WebSockets are not implemented.

---

# 8. Payments — Razorpay

### `POST /payments/create-order`
Create a Razorpay order.

**Payment types (locked)**
- `TEAM_REGISTRATION` — paid by the team leader for the whole team
- `SOLO_REGISTRATION` — paid by the solo participant

There is **no** `TEAM_MEMBER_TOPUP` and no member payment.

**Does**
- Calculate/validate the amount server-side (single event fee).
- Create the Razorpay order.
- Persist the payment.
- Return Razorpay checkout information.

**Access:** Authenticated user (payer must be the solo registrant or team leader).

### `POST /payments/verify`
Verify a Razorpay payment server-side.

**Does**
- Verify the Razorpay signature.
- Update `PAYMENTS`.
- Confirm the relevant registration or activate the pending team member.
- Trigger QR/receipt/notification processing.

**Access:** Authenticated user for their own payment, with server-side ownership validation.

### `POST /payments/webhook`
Receive Razorpay webhook events.

**Does**
- Recover/update payment state when the browser closes or callback fails.
- Process relevant payment/refund events idempotently.

**Access:** Razorpay webhook authentication/signature only.

### `GET /payments/{payment_id}`
Return payment status/details for the payer.

**Access:** Payment owner or authorized admin.

---

# 9. Refunds

### `POST /admin/payments/{payment_id}/refund`
Initiate/record a Razorpay refund.

**Does**
- Create/record refund information.
- Update payment state.
- Trigger refund notification.
- Apply the appropriate registration/team-member state changes.

**Access:** Super Admin only.

---

# 10. Email / Notifications

Email delivery is normally triggered by successful domain actions such as payment verification, joining a team, or team completion. The notification endpoints below are for explicit delivery/retry operations.

### `POST /notifications/payment-confirmation`
Send a payment confirmation email/notification.

**Includes**
- Payment confirmation.
- Registration details.
- Receipt information/link.
- QR information where applicable.
- Event WhatsApp group link.

**Access:** Internal notification service or authorized admin; not a general public endpoint.

### `POST /notifications/member-confirmation`
Send a member confirmation email/notification.

**Includes**
- Event/team details.
- Member confirmation.
- Individual QR.
- Event WhatsApp group link.

**Access:** Internal notification service or authorized admin; not a general public endpoint.

### `POST /notifications/team-member-joined`
Notify the team leader when a member successfully joins.

**Access:** Internal notification service or authorized admin.

### `POST /notifications/team-completed`
Notify the team leader when the team reaches the required size.

**Access:** Internal notification service or authorized admin.

### `POST /notifications/resend`
Resend an existing notification.

**Access:** Notification owner for their own related registration/member notification, or authorized admin.

**Important:** Never allow arbitrary callers to specify an arbitrary recipient email/registration.

### `POST /admin/notifications/announcement`
Send an event announcement.

**Access:** Admin with notification/announcement permission.

### `POST /admin/notifications/reminder`
Send an event reminder.

**Access:** Admin with notification/reminder permission.

---

# 11. QR Management

### `POST /qr/generate`
Generate or regenerate a QR for an eligible participant.

**Ownership**
Exactly one of:
- `team_member_id`
- `registration_id`

**Access:** Internal registration service or authorized admin. Normal users receive QR through their registration/member confirmation flow.

### `GET /qr/{qr_id}`
Return QR status and associated participant/registration information.

**Access:** QR owner or authorized admin.

---

# 12. Attendance

### `POST /attendance/scan`
Scan a participant QR at an attendance checkpoint.

**Validates**
- QR token.
- QR active state.
- Payment/registration eligibility.
- Checkpoint state.
- Duplicate/repeat scan configuration.

**Results**
- `SUCCESS`
- `DUPLICATE`
- `INVALID`
- `NOT_PAID`

**Access:** Authenticated scanner/admin with attendance-scan permission.

### `GET /admin/attendance`
Return attendance and immutable scan history.

**Access:** Admin with attendance-read permission.

### `GET /admin/attendance/checkpoints`
List attendance checkpoints.

**Access:** Admin with attendance/checkpoint permission.

### `POST /admin/attendance/checkpoints`
Create an attendance checkpoint.

**Access:** Admin with checkpoint-management permission.

### `PATCH /admin/attendance/checkpoints/{checkpoint_id}`
Update checkpoint name, location, active state and duplicate/repeat behavior.

**Access:** Admin with checkpoint-management permission.

### `POST /admin/attendance/manual`
Manually mark attendance while preserving an audit trail.

**Access:** Admin with manual-attendance permission.

---

# 13. Admin Authentication / RBAC

### `GET /admin/me`
Return the current administrator's:
- Admin user ID.
- Role.
- Permissions.
- Active/inactive state.

**Access:** Authenticated user with admin access.

### `GET /admin/roles`
List available roles and their permissions.

**Access:** Super Admin only.

### `POST /admin/roles`
Create a role.

**Does**
- Create a role.
- Assign its permission set.

**Access:** Super Admin only.

### `PATCH /admin/roles/{role_id}`
Update a role's name/description and permission set.

**Access:** Super Admin only.

### `GET /admin/admin-users`
List users with admin access.

**Access:** Super Admin only.

### `POST /admin/admin-users`
Grant a user admin access and assign a role.

**Access:** Super Admin only.

### `PATCH /admin/admin-users/{admin_user_id}`
Change an admin user's role or deactivate admin access.

**Access:** Super Admin only.

---

# 14. Admin Dashboard

### `GET /admin/dashboard`
Return dashboard statistics covering:
- Registrations.
- Teams.
- Payments.
- Attendance.
- Event activity.

**Access:** Admin with dashboard permission.

---

# 15. Admin Event Management

### `POST /admin/events`
Create an event.

**Access:** Admin with event-management permission.

### `PATCH /admin/events/{event_id}`
Update event metadata including:
- Description.
- Dates.
- Venue.
- Coordinator.
- WhatsApp group link.
- Status where permitted.

**Access:** Admin with event-management permission.

### `PATCH /admin/events/{event_id}/registration-rules`
Update:
- Team minimum/maximum.
- Individual registration.
- Pricing model.
- Capacity type.
- Member registration mode.
- Registration dates.
- Dynamic fields.
- QR requirement.

**Access:** Admin with event-rule-management permission.

### `POST /admin/events/{event_id}/close`
Manually close registration.

**Access:** Admin with event-management permission.

### `POST /admin/events/{event_id}/open`
Open registration where allowed by configuration.

**Access:** Admin with event-management permission.

---

# 16. Admin Participant Management

### `GET /admin/participants`
Search/filter participants by:
- Name.
- Email.
- Registration ID.
- Team.
- Phone.
- QR ID.
- Event.
- Payment state.
- Registration state.
- Attendance state.

**Access:** Admin with participant-read permission.

### `PATCH /admin/participants/{participant_id}`
Edit permitted participant/profile details.

**Access:** Admin with participant-edit permission.

### `POST /admin/participants`
Manually register a participant.

**Access:** Admin with participant-registration permission.

---

# 17. Admin Team Management

### `GET /admin/teams`
List/search teams by event, team name, leader, status, payment state and completion state.

**Access:** Admin with team-read permission.

### `PATCH /admin/teams/{team_id}`
Update permitted team information.

**Access:** Admin with team-edit permission.

### `POST /admin/teams/{team_id}/transfer-leadership`
Transfer team leadership and update the relevant `TEAM_MEMBERS.role` and `TEAMS.leader_profile_id`.

**Access:** Super Admin or admin with leadership-transfer permission.

### `POST /admin/teams/{team_id}/cancel`
Cancel a team and trigger applicable registration/payment/refund processing.

**Access:** Super Admin only.

---

# 18. Admin Registration Management

### `GET /admin/registrations`
Search/list registrations by event, participant, team, registration status and payment status.

**Access:** Admin with registration-read permission.

### `PATCH /admin/registrations/{registration_id}`
Make permitted administrative corrections.

**Access:** Admin with registration-edit permission.

### `POST /admin/registrations/{registration_id}/cancel`
Cancel a registration.

**Access:** Super Admin only where cancellation/refund is involved.

---

# 19. Admin Payment Management

### `GET /admin/payments`
Search/list payments by:
- Participant.
- Event.
- Team.
- Payment ID.
- Razorpay order/payment ID.
- Payment type.
- Status.

**Access:** Admin with payment-read permission.

### `POST /admin/payments/{payment_id}/refund`
Refund a payment.

**Access:** Super Admin only.

---

# 20. Admin Notifications

### `POST /admin/notifications/announcement`
Send an event announcement.

**Access:** Admin with announcement permission.

### `POST /admin/notifications/reminder`
Send event reminders.

**Access:** Admin with reminder permission.

---

# 21. Admin Exports

### `GET /admin/exports/registrations`
Generate/download registration Excel export.

**Access:** Admin with export permission.

### `GET /admin/exports/payments`
Generate/download payment report.

**Access:** Admin with export permission.

### `GET /admin/exports/attendance`
Generate/download attendance/scan-history report.

**Access:** Admin with export permission.

---

# 22. Cart / Multi-Event Checkout — Product Decision Required

The finalized schema technically permits multiple `REGISTRATIONS` to reference the same `PAYMENTS` row, but the current API design is event-by-event.

The current endpoint set therefore implements:

```text
Event A
  -> registration
  -> payment
```

and not:

```text
Cart
  -> Event A registration
  -> Event B registration
  -> Event C registration
  -> one Razorpay payment
```

**Product decision required before implementation:**

- If multi-event checkout is intentionally dropped, keep the current event-by-event payment API.
- If multi-event checkout is required, introduce a cart/checkout abstraction and make `POST /payments/create-order` accept multiple registration/payment targets.

Do not implement multi-event checkout accidentally just because the database can technically support shared payments.

---

# 23. Backend Internal Functions

These are service functions rather than public HTTP endpoints.

### Registration validation
Validate event status, deadlines, capacity, team rules, duplicate membership and eligibility.

### Team slot reservation
Atomically reserve team capacity.

### Payment verification
Verify Razorpay signatures and update payment state.

### QR generation
Create secure, unique QR tokens for eligible participants.

### Receipt generation
Generate receipts and expose them through the receipt endpoint.

### Email/notification service
Send payment, registration, member, QR, team and event notifications.

### Deadline closure
Prevent new registrations after the configured deadline.

### QR/check-in validation
Validate QR state and checkpoint-specific duplicate behavior.

### Team completion detection
Detect when a team reaches the required size and trigger the completion notification.

### Refund processing
Process/record authorized refunds.

---

# 24. Core Registration Flows

## Solo

```text
Google login
  -> create registration
  -> create payment
  -> Razorpay
  -> server-side verification
  -> registration CONFIRMED
  -> QR_CODES.registration_id
  -> confirmation + QR + receipt
```

## Team (leader pays once)

```text
Create team
  -> leader TEAM_MEMBER (PENDING_PAYMENT until pay)
  -> team registration (team_id)
  -> leader pays TEAM_REGISTRATION
  -> team = PAID, registration CONFIRMED, leader ACTIVE + QR
  -> members join via invite
  -> members ACTIVE (no payment) + member QR
  -> team COMPLETE when roster full
```

**Only the solo participant or team leader pays. Team members never pay.**

---

# 25. Implementation Rules

- Backend is the source of truth for payment status.
- Verify Razorpay signatures server-side.
- Capacity and team-slot checks must be atomic.
- Team invitation links are reusable and non-expiring unless explicitly revoked.
- A participant cannot belong to multiple teams in the same event.
- Duplicate join requests must be idempotent.
- `PAID` does not mean a team is complete and does not lock the roster.
- Team members never pay and never create a payment or registration on join.
- Every eligible participant has exactly one QR ownership path.
- QR tokens must be secure/unguessable and must not expose internal IDs.
- Attendance scans are immutable history.
- Admin permissions must be enforced server-side.
- Super Admin-only actions must be protected server-side.
- Notification endpoints must never allow arbitrary recipient selection.
- Domain actions such as successful payment/member activation should trigger notifications through the backend notification service rather than relying on the browser to call an email endpoint.
