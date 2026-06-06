# General
1. pick_up_ticket
- including a problem statement

2. assert_coding
- Is this a coding ticket

3. open_branch

4. Plan_question_loop
- Goal: create a super concise, high level implementation plan (TODO determine what high level exactly means)
- Mark subsections as "needs additional research", if 

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
9.1. 

11. review
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