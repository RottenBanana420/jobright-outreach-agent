# Jobright Outreach

Follow-up outreach to people at companies where the user has already applied for a job through Jobright.

## Applications

**Application**:
A job in Jobright's Applied list, identified by its Jobright job ID, with an Applied-on date and a tracking status.
_Avoid_: Job, posting, submission

**Today's Application**:
An Application whose status is Applied and whose Applied-on date is today in the user's local timezone.
_Avoid_: Confirmed submission, new application

**Agent-Applied**:
An Application that Jobright's own agent submitted rather than the user, shown with an "Applied by Agent" label.

**Match Score**:
Jobright's overall percentage fit for an Application, with sub-scores for experience level, skills, and either industry experience or education.
_Avoid_: Fit score, rating

**Shortlist**:
Today's Applications ordered by Match Score, cut to at most 10 Company Groups, that get outreach.
_Avoid_: Top 10, selection, priority list

**Company Group**:
All of the user's open Applications at one company, treated as one unit for outreach so no Contact is written to separately about each role.

**Run**:
One start-to-finish pass over Today's Applications, from the start command until every approved Outreach Email is sent or the user stops it. The Mac is kept awake for the length of a Run.
_Avoid_: Job, session, batch

## People and messages

**Contact**:
A person Jobright suggests under Insider Connection for an Application, with a name, title, and a Jobright-provided email address.
_Avoid_: Lead, prospect, recipient

**Contact Category**:
The kind of Contact: Recruiter, Hiring Leader, Engineer, or Other Recruiting.

**Unverified Address**:
A Contact email that Jobright says it found from public information and may be wrong.

**Jobright Draft**:
The subject and body Jobright pre-fills in its "Connect Via Email" dialog for a Contact.
_Avoid_: Template, suggested email

**Outreach Email**:
The single email this project writes to one Contact about one Company Group, with exactly one recipient and one resume attached.
_Avoid_: Draft (unqualified), message, mail

**Resume Variant**:
One of the user's role-specific resume PDFs (Software Engineer, AI Engineer, ML Engineer, Data Engineer, Forward Deployed Engineer). Each Outreach Email attaches exactly one, chosen from the role title.
_Avoid_: Resume version, CV

**Approval**:
The user's explicit yes for one Outreach Email, given in the review screen. Only approved Outreach Emails are ever sent.
