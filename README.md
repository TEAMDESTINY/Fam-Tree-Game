
# Fam Tree v2

This is a telegram family tree game bot, special thanks to https://t.me/fam_tree the original game bot which is now dead. I heard they lost code that's why its no more.

So some of my friends wanted to play it and I also was thinking to work on such side project so slowly i was building this one with the help of AI to speed up process as i am studying right now and my focus is on it mainly.

This game economy is not stable, I know and i don't plan to fix it because its just a fun game and this economy keep my friends happy when they are playing it.

I mainly took inspiration from official bot guide, they gave good example which i was able to implement https://telegra.ph/Fam-Tree-Guide-03-07

### Request to users of bot

Guys you can see /craft recipes from this codebase but please don't cheat in game. Try to find those craft recipes by yourself, it's more fun that way.


### Comments on codebase

There are pyvis version of code for tree and friend circle, my first plan was to use it for visualisation but i realised it's not good if i want proper control over image generation. So their code file is still there, i planned to use it when i will work on webapp to view family tree interactively.

I have added credit file for little alchemy craft  recipes sources, the scripts folder is where you can see raw written files i used for scraping fanon elements and creating it json file and also uploading emoji to telegram,

### Potential unfixed bugs:

Mainly issue in family tree itself, at start i was allowing people to have more than one spouse and can be child of multiple people. Which was for fun but it was messing up tree. So i slowly fixed it and i kept the feature so user can make other sibling without needing to have parents. And some other things are also different, So right now i think you may see bug in things like you can't adopt someone when you should be (because they appear in child generation)

One more thing, i tried to allow marriage between different generation people because in real life it does happens right...


### AI USAGE

Mainly i used AI to speed up implementation of features, move codebase from aiogram(BOT API) to kurigram(MTProto API) and some other task. This project is mixture of code written by Claude code, Gemini and my own. Including Claude chat (after i didn't renew claude code).


### Why No Git history?

So init commit is big, no old history shared why? Well i wasn't planning to share the codebase soon so i started using actual people name as example for me to understand fixes i did to family tree.

It would be annoying to rebase it out, so i am doing fresh commit 

### Contribution

Anyone is open to give their feedback and contribute to this project, but before you open a PR. Make sure to confirm things with me in issues section. It would be wrong if you work on something and i once already considered that but decided to not do this and do something else

