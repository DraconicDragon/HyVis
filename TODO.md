# TODO

- instead of using tag query from config, pull files from an open page in hydrus, saves hassle of dealing with AND/OR stuff and can use all the system tags

- hydrus file download to memory for remote hydrus

- change/make different system because categories can be arbitrary, and "rating" tags for e6 models like JTP3 are in "meta" category, not separate "rating"
- output tag filter
  - Scenario: I want to only have rating tags sent to hydrus; this works with setting "rating" category as only output category, but gets complicated when using models that dont have separate rating category like JTP-3 which put rating tags in "meta" category with other non-rating tags

- support for "AND" "OR" etc for tag queries

- file domains - unlikely, i dont use them at all and dont plan to

- more qol stuff
  - Model info should be easier available, eg during errors or trying to discover them and stuff
  - more display, eg what device ended up being used

- README stuff

- i completely forgot about this and the project/config isnt set up at all for this but...
  - Scenario: 2 or more models active in the same config - In theory user could want to have model 1's tags sent to tag service A while model 2's tags should be sent to tag service B
    - making 2 separate runs would mean reading the same files for a second time (assuming that in the scenario the 2 models both fit in the first place)
    - pain, do I need new config format
