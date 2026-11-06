# HappyRobot Carrier Agent: Build Steps

## Overview

| # | Phase | Focus |
|---|-------|-------|
| 1 | [Set Up HappyRobot](#step-1-set-up-happyrobot) | Workflow, trigger, voice agent |
| 2 | [Build Your Python Backend](#step-2-build-your-python-backend) | FastAPI, FMCSA, OTP, TMS |
| 3 | [Connect Your Backend to HappyRobot](#step-3-connect-your-backend-to-happyrobot) | Tools and workflow conditions |
| 4 | [Build the Carrier Conversation](#step-4-build-the-carrier-conversation) | Call flow, start to finish |
| 5 | [Set Up HappyRobot Twin](#step-5-set-up-happyrobot-twin) | Data and audit trail |
| 6 | [Build HappyRobot Apps Dashboard](#step-6-build-happyrobot-apps-dashboard) | Metrics and review |
| 7 | [Testing and QA](#step-7-testing-and-qa) | Happy paths, edge cases, security |
| 8 | [Deployment](#step-8-deployment) | Docker and cloud |
| 9 | [Final Deliverables](#step-9-final-deliverables) | Email, docs, demo |

---

## Step 1: Set Up HappyRobot

- [x] Create a new workflow in HappyRobot.
- [x] Configure a **web call trigger**.
- [x] Create an **AI voice agent**.
- [x] Define the agent's instructions and conversation flow.

---

## Step 2: Build Your Python Backend

**Server**
- [x] Create a **FastAPI** server.
- [x] Expose all functions as **authenticated API endpoints**.

**Carrier verification**
- [x] Integrate with the **FMCSA API** to verify trucking companies.
- [x] Implement **OTP** generation and verification.

**Loads (legacy TMS)**
- [x] Connect to the legacy TMS using **TCP sockets**.
- [x] Implement load search and load detail retrieval.
- [x] Implement rate negotiation logic (**max 3 counter rounds**).
- [x] Implement tentative load booking.

---

## Step 3: Connect Your Backend to HappyRobot

- [x] Deploy your Python backend.
- [x] Register your API endpoints as **tools** in HappyRobot.
- [x] Configure the agent to call the appropriate tools.
- [x] Add workflow conditions for **success** and **failure** scenarios.

---

## Step 4: Build the Carrier Conversation

The call should follow this order:

1. Greet the carrier and collect their **MC number**.
2. Verify their operating authority through **FMCSA**.
3. Send and validate an **OTP**.
4. Ask for their preferred **route** and **equipment type**.
5. Search the TMS for matching loads.
6. Present available loads to the carrier.
7. Negotiate a rate **without exceeding the confidential maximum**.
8. Tentatively book the load once a rate is agreed.
9. Mock the handoff to a senior representative.
10. End the call professionally.

---

## Step 5: Set Up HappyRobot Twin

- [ ] Create data structures for:
  - Call sessions
  - Carriers
  - Negotiations
  - Bookings
- [ ] Log verification results, tool actions, offers, and call outcomes.
- [ ] Store successful bookings **and** failed interactions.
- [ ] Maintain an **audit trail** for every call.

---

## Step 6: Build HappyRobot Apps Dashboard

- [ ] Display total calls and successful negotiations.
- [ ] Show verification failures and unsuccessful calls.
- [ ] Display tentative bookings awaiting **senior-rep review**.
- [ ] Allow operations managers to **review and flag** calls.

---

## Step 7: Testing and QA

| Category | Test case |
|----------|-----------|
| Happy path | Successful carrier verification and booking |
| Verification | Invalid MC numbers |
| Verification | Incorrect OTPs |
| Loads | No available loads |
| Negotiation | Offers exceeding the maximum rate |
| Negotiation | More than three counter rounds |
| TMS reliability | Timeouts |
| TMS reliability | Malformed responses |
| Security | Prompt injection |
| Security | Attempts to bypass verification |

- [ ] Define **North Star KPIs**.
- [ ] Document actual test results.

---

## Step 8: Deployment

- [x] Dockerize your backend.
- [x] Deploy to a cloud environment with **a single command**.
- [ ] Verify HappyRobot can reach your deployed APIs.
- [ ] Run an **end-to-end test**.

---

## Step 9: Final Deliverables

- [ ] Summary email to the prospect
- [ ] Technical build description document
- [ ] Private GitHub repository shared with reviewers
- [ ] HappyRobot workflow link
- [ ] Five-minute walkthrough video showing the agent, dashboard, and QA results
