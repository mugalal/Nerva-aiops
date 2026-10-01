from .states import IncidentStatus
from .machine import can_transition


# print(
#     can_transition(
#         IncidentStatus.DETECTED,
#         IncidentStatus.CORRELATING
#     )
# )

# print(
#     can_transition(
#         IncidentStatus.DETECTED,
#         IncidentStatus.RESOLVED
#     )
# )




# print(
#     transition(
#         IncidentStatus.DETECTED,
#         IncidentStatus.CORRELATING
#     )
# )

# print(
#     transition(
#         IncidentStatus.DETECTED,
#         IncidentStatus.RESOLVED
#     )
# )

from .states import IncidentStatus
from .machine import transition


print(
    transition(
        IncidentStatus.DETECTED,
        IncidentStatus.CORRELATING
    )
)

try:
    transition(
        IncidentStatus.DETECTED,
        IncidentStatus.RESOLVED
    )
except ValueError as error:
    print(error)