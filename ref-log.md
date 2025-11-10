After splitting the application into Planner (offline) → Reviewer (online verification), the greatest gain is that the clearer the division of labor, the more stable the output. Planner does not search the Internet. Instead, he arranges the daily itinerary based on common sense and rhythm, and adheres to the budget. The Reviewer only checks key facts such as "opening hours/closing days, ticket prices and whether a reservation is required", and makes the least necessary modifications to the evidence rather than starting from scratch. This pace of "completing the draft first and then conducting a physical examination" has significantly reduced illusions and unnecessary alterations.

In the implementation, I focused on solving three types of problems:
1.Priority for authoritative sources: When searching, use site: Filter first official sources. Third parties need to cross-verify with the official ones. In case of any conflict, the official ones shall prevail. If can't find it, just say so. Don't guess.

2.The table is fragile: The model often gets stuck in cells or bullet points, causing rendering crashes. In the prompt, I fixed the header template, prohibited pipes/bullet points, and required that only change lines be output, significantly enhancing stability.

3.How much to change is just right: Clearly define the boundaries of the Reviewer - make changes only when there is evidence, and only at the critical points. Keep the same currency as Planner (the default in Europe is €), and provide a rhythm prompt of "2-4 hours at the museum, 60-90 meters for meals" to avoid overloading the day.

In terms of style and readability, I adopted two small designs: directly marking the items that need to be reserved (pre-book, timed entry) within the cells; Replace long links with short source tags (such as [louvre.fr]), which is both transparent and concise. 



External tools and GenAI assistance
The Streamlit template provided by the course (including sidebar tool logs)
Tavily: internet_search real-time search
ChatGPT: I use gpt to search for the website addresses of relevant official websites as an example