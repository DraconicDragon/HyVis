# TODO

- supported models doc should probably show tag amount and link to them or something, also have vibe have some description and note field(s) or such to be able to give more info on a model or family

- Character IP mapping support
  - (vibe) also find out how to make my own or if i just need full metadata for that and have the math done on full post metadata to see how often series tags are together with what character tags, maybe some of the e6 people already have some process open source

- (vibe - ModelPlugin Metadata) Recommended thresholds for each model, in extra column in SUPPORTED_MODELS.md

- show at the end of processing summary another summary of why files were skipped, most often expected cause is likely no tags were emitted (eg all below threshold)
  - as example, this is possible if only rating tags are to be emitted from pixai-tagger-v1.0 and the recommended threshold is used, however the recommended threshold for rating category is an averaged out score of each rating tag, and in reality the recommended thresholds for each rating tag are WIDELY different (see best_threshold csv of animetimm tags) so for general, suggestive and questionable the ideal threshold is actually lower (i think)

- Consider: improve descriptiveness of config option's descriptions in config.py so they are more informative as tooltips in the GUI

- Save logs to files with rotation so logs don't fill up space too much
  - Logs in terminal fight for space with the progress printing during inference

## TODO For User Interface

- Persistent data logic (should be able to just use system-native appdata/.config or whatever folders) for:
  - settings
  - last used config file(s)

- Store intermediate in-edit config as .tmp file or something so it's not lost by accident
  - Think of if discard will delete the temp config or not
  - Option to restore from this temp config on program start if it wasn't saved
    - if the temp config was saved, then there should be no .tmp file

- Show extra validation error/warning (warning needs to be implemented, separate from errors) when:
  - E: Unkown tag service key is set in a combobox (eg when user loaded old config but the tag service doesnt exist anymore)
  - W: When a user inputted category is present but appears never in relation to any model ID; its an unkown category
    - Note on reasoning behind this: The metadata that presents which categories a model plugin/ID outputs is not a hard requirement, custom model files can also be used which may be finetunes and simply just work with existing code, but output undocumented categories

- Low priority: Create custom base/primitive widgets like Custom QButtons and use throughout UI to have better control over them
  - Increase size of widgets by a bit so they are easier to hit, default Qt is kinda ass, but definitely not unusable, might introduce other issues so idk
    - Change of UI layout to be in 2 columns (like add/remove tags) may be beneficial for efficient space usage

- Least priority: polish widget styling so it works with light mode better (the custom style for the page switcher list view on the left is too bright for light mode, text mainly)

### Potential Settings To Implement

- Option for default config to load upon program start
  - Option to load last recently used / .tmp ("Where you left off")
- Automatically open preview pages in Hydrus - if possible - on Launch in terminal popup spawn; or after terminal is launched (would remove the no preview cli arg)
- Configurable float/SpinBox stepping (additive)

## lower priority (in order, mostly)

- (vibe) store param count for each model(-plugin)
  - what is this useful for
  - I'd have to calculate it using numel()/sfts metadata/onnx graph for each model
    - animetimm models already have info available on their readmes but not the rest
    - No, jtp3/hydra 3.5 is not 400m params, thats the siglip2 naflex base, jtp3's hydra head and other custom stuff makes it 501m params
  - could use jupyter notebook + google colab i guess to download each model and calc count and save

- its gonna be weird but: video/animation support, could support multiple modes
  - every frame (too heavy but an option, maybe for gifs/very short vids)
  - every nth frame (could do fps percentage based or hard user-set frame intervals)
  - keyframes only (if easily possible) - **LIKELY BEST OPTION**
  - do analysis and find frames with cuts or scene changes

- better progress printing for preflight work (file metadata fetching)

- (need more info) magic byte fallback for metadata fetch incase hydrus mime type is "unknown" (suggesting its not set, which would be unexpected)

- (v2) support for removing tags if confidence is below a certain threshold
  - can be useful if there's wrong tags either from original source or from old model i guess?

- configurable metadata fetch batch size (currently 256 hardcoded)

- more utilities for db like clear-cache? model_id specific stuff maybe?

- option to set auto download (huggingface download) off since vibe has it too
  - or just tell people to prefix source option with `local:`

- [x] Support for pulling files from an open page in hydrus
  - [ ] Could also work with tag queries - in the scope of pages, we apply the query instead of just all tags in a domain to the domain, but only on files that are in the page(s) - need to confirm if this will actually work easily

- [x] Preview for tag queries using an open page in Hydrus
  - [ ] mark experimental since i think API says that these endpoints are experimental

- Hydrus file download to memory for remote clients/file locations
  - BTW: Hydrus has option to compress files on host before serving over API, may be useful?

- file domains - unlikely, i dont use them at all and dont plan to
