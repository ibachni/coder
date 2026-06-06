# Research
1. pick_up_ticket
- including a problem statement

2. assert_coding
- Is this a coding ticket

3. open_branch

4. Plan_question_loop
- Goal: create a super concise, high level researchplan (TODO determine what high level exactly means; high level calibration)
- Mark subsections as "needs additional research", if they are they need additional research

5. surface_questions
- create a initial super concise implementation plan

6. get_user_answer
- Push the questions to a UI

7. implement_user_answer
- Update the concise implementation plan

8. decide_if_additional_questions
- Check the highlevel implementation plan if the are additional questions -> go back to surface questions

9. Get_human_in_the_loop plan approval

10. execution
- 10.1. Go over individual sub task
- 10.2. review
- 10.3. fix issues
- 10.4. store result in a good format

11. review overall execution
- keep problem statement in mind
- find issues in the execution

12. plan_issues_fixes
- if issues:
- plan out issues fixes
- surface perhaps questions

13. execution


14. commit_push

15. merge


# Idea:
- include an "autonomy" variable which determines, how autonomous the agent should decide questions

# Question:
- how can I reduce token consumption? One continuous session across certain questions?

# basically always the same loop
- Consider problem statement
- Create high level plan
- Ask questions, implement answers into plan
- until: Have a clear high level plan
- Go into first sub items (should be limited in size) (loop)
    - create a more detailed plan
    - ask questions, implement answers into plan
    - until: have a clear low level item plan
    - execute
    - review
    - Fix issues (Loop)
- all sub items done
- review overall 
- fix overall (Loop)


# Problem: how detailed; orchestrator agent vs hardcoded steps

# Go towards a "skill-based" & stages
1. High level planning stage
- Create concise highlevel plan in stages
- Surface questions -> automatically surfaced
- Implement answers into text + problem statement
- Overall plan review 

2. Sub-stage
- Create low level plan
- Surface questions
- Implement answers into text
- Execute
- review execution
- fix issues

3. Overall review
- Review total implementation (branch diff against updated problem statement)
- fix issues

Question: how to decide if any stage is done?