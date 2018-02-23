
import sys
from collections import OrderedDict

from face.utils import unwrap_text
from face.parser import Parser, Flag, ArgumentParseError, FaceException, ERROR
from face.helpers import HelpHandler
from face.middleware import (inject,
                             is_middleware,
                             face_middleware,
                             check_middleware,
                             get_middleware_chain,
                             resolve_middleware_chain,
                             _BUILTIN_PROVIDES)

from boltons.iterutils import unique

class CommandLineError(FaceException, SystemExit):
    def __init__(self, msg, code=1):
        SystemExit.__init__(self, msg)
        self.code = code


def _get_default_name(frame_level=1):
    # TODO: is this a good idea? What if multiple parsers are created
    # in the same function for the sake of subparsers. This should
    # probably only be used from a classmethod or maybe a util
    # function.  TODO: what happens if a python module file contains a
    # non-ascii character?
    frame = sys._getframe(frame_level + 1)
    mod_name = frame.f_globals.get('__name__')
    if mod_name is None:
        return 'COMMAND'
    module = sys.modules[mod_name]
    if mod_name == '__main__':
        return module.__file__
    # TODO: reverse lookup entrypoint?
    return mod_name


def _docstring_to_doc(func):
    doc = func.__doc__
    if not doc:
        return ''

    unwrapped = unwrap_text(doc)
    try:
        first_graf = [g for g in unwrapped.splitlines() if g][0]
    except IndexError:
        return ''

    ret = first_graf[:first_graf.find('.')][:80]
    return ret


def default_print_error(msg):
    return sys.stderr.write(msg + '\n')


DEFAULT_HELP_HANDLER = HelpHandler()


# TODO: add flags, subcommands as parameters so that everything can be
# initialized in one call.
class Command(Parser):
    def __init__(self, func, name=None, doc=None, posargs=False, middlewares=None,
                 print_error=None, help=DEFAULT_HELP_HANDLER):
        name = name if name is not None else _get_default_name()

        if doc is None:
            doc = _docstring_to_doc(func)

        super(Command, self).__init__(name, doc, posargs=posargs)
        # TODO: properties for name/doc/other parser things

        self._path_func_map = OrderedDict()
        self._path_func_map[()] = func

        middlewares = list(middlewares or [])
        self._path_mw_map = OrderedDict()
        self._path_mw_map[()] = []
        self._path_wrapped_map = OrderedDict()
        self._path_wrapped_map[()] = func
        for mw in middlewares:
            self.add_middleware(mw)

        if print_error is None or print_error is True:
            print_error = default_print_error
        elif print_error and not callable(print_error):
            raise TypeError('expected callable for print_error, not %r'
                            % print_error)
        self.print_error = print_error

        self.help_handler = help
        if help:
            if help.flag:
                self.add(help.flag)
            if help.subcmd:
                self.add(help.func, help.subcmd)  # for 'help' as a subcmd

        return

    @property
    def func(self):
        return self._path_func_map[()]

    def add(self, *a, **kw):
        subcmd = a[0]

        if not isinstance(subcmd, Command) and callable(subcmd):
            if is_middleware(subcmd):
                return self.add_middleware(subcmd)

            subcmd = Command(*a, **kw)  # attempt to construct a new subcmd
        if isinstance(subcmd, Command):
            self.add_command(subcmd)
            return subcmd
        flag = a[0]
        if not isinstance(flag, Flag):
            flag = Flag(*a, **kw)  # attempt to construct a Flag from arguments
        super(Command, self).add(flag)

        return flag

    def add_command(self, subcmd):
        if not isinstance(subcmd, Command):
            raise TypeError('expected Command instance, not: %r' % subcmd)
        self_mw = self._path_mw_map[()]
        super(Command, self).add(subcmd)
        # map in new functions
        for path in self.subprs_map:
            if path not in self._path_func_map:
                self._path_func_map[path] = subcmd._path_func_map[path[1:]]
                sub_mw = subcmd._path_mw_map[path[1:]]
                self._path_mw_map[path] = self_mw + sub_mw  # TODO: check for conflicts
        return

    def add_middleware(self, mw):
        if not is_middleware(mw):
            mw = face_middleware(mw)
        check_middleware(mw)

        for flag in mw._face_flags:
            self.add(flag)

        for path, mws in self._path_mw_map.items():
            self._path_mw_map[path] = [mw] + mws  # TODO: check for conflicts

        return

    def get_flag_map(self, path=(), with_hidden=True):
        """Command's get_flag_map differs from Parser's in that it filters
        the flag map to just the flags used by the endpoint at the
        associated subcommand *path*.
        """
        flag_map = super(Command, self).get_flag_map(path=path, with_hidden=with_hidden)
        dep_names = self.get_dep_names(path)

        # TODO: if dep_names includes args_ or flags_ we need to
        # bypass the default filtering and either let all flags
        # through or just the ones declared by some decorator.
        return dict([(k, f) for k, f in flag_map.items() if f.name in dep_names
                     or f is self.flagfile_flag or f is self.help_handler.flag])

    def get_dep_names(self, path=()):
        func = self._path_func_map[path]
        if func is ERROR:  # TODO: check back if we're still supporting this
            raise ValueError('no handler specified for command path: %r' % path)
        mws = self._path_mw_map[path]

        _, mw_chain_args, _ = resolve_middleware_chain(mws, func, preprovided=[])

        return sorted(mw_chain_args)

    def prepare(self, paths=None):
        if paths is None:
            paths = self._path_func_map.keys()

        for path, func in self._path_func_map.items():
            mws = self._path_mw_map[path]
            flag_names = [f.name for f in self.get_flags(path=path)]
            provides = _BUILTIN_PROVIDES + flag_names
            wrapped = get_middleware_chain(mws, func, provides)

            self._path_wrapped_map[path] = wrapped

        return

    def run(self, argv=None, extras=None):
        kwargs = dict(extras) if extras else {}
        # TODO: turn parse exceptions into nice error messages
        try:
            prs_res = self.parse(argv=argv)
        except ArgumentParseError as ape:
            msg = 'error: ' + self.name
            if getattr(ape, 'subcmds', None):
                msg += ' ' + ' '.join(ape.subcmds or ())
            try:
                e_msg = ape.args[0]
            except (AttributeError, IndexError):
                e_msg = ''
            if e_msg:
                msg += ': ' + e_msg
            cle = CommandLineError(msg)
            self.print_error(msg)
            raise cle

        kwargs.update({'args_': prs_res,
                       'cmd_': self,  # TODO: see also command_, should this be prs_res.name, or argv[0]?
                       'subcmds_': prs_res.subcmds,
                       'flags_': prs_res.flags,
                       'posargs_': prs_res.posargs,
                       'post_posargs_': prs_res.post_posargs,
                       'command_': self})
        kwargs.update(prs_res.flags)

        if self.help_handler and prs_res.flags.get(self.help_handler.flag.name):
            return inject(self.help_handler.func, kwargs)

        self.prepare(paths=[prs_res.subcmds])

        # default in case no middlewares have been installed
        func = self._path_func_map[prs_res.subcmds]
        wrapped = self._path_wrapped_map.get(prs_res.subcmds, func)

        return inject(wrapped, kwargs)


"""Middleware thoughts:

* Clastic-like, but single function
* Mark with a @middleware(provides=()) decorator for provides

* Keywords (ParseResult members) end with _ (e.g., flags_), leaving
  injection namespace wide open for flags. With clastic, argument
  names are primarily internal, like a path parameter's name is not
  exposed to the user. With face, the flag names are part of the
  exposed API, and we don't want to reserve keywords or have
  excessively long prefixes.

* add() supports @middleware decorated middleware

* add_middleware() exists for non-decorated middleware functions, and
  just conveniently calls middleware decorator for you (decorator only
  necessary for provides)

Also Kurt says an easy way to access the subcommands to tweak them
would be useful. I think it's better to build up from the leaves than
to allow mutability that could trigger rechecks and failures across
the whole subcommand tree. Better instead to make copies of
subparsers/subcommands/flags and treat them as internal state.


TODO:

In addition to the existing function-as-first-arg interface, Command
should take a list of add()-ables as the first argument. This allows
easy composition from subcommands and common flags.

# What goes in a bound command?

* name
* doc
* handler func
* list of middlewares
* parser (currently contains the following)
    * flag map
    * PosArgSpecs for posargs, post_posargs
    * flagfile flag
    * help flag (or help subcommand)

TODO: allow user to configure the message for CommandLineErrors
TODO: should Command take resources?
TODO: should version_ be a built-in/injectable?

Need to split up the checks. Basic verification of middleware
structure OK. Can check for redefinitions of provides and
conflicts. Need a final .check() method that checks that all
subcommands have their requirements fulfilled. Technically a .run()
only needs to run one specific subcommand, only thta one needs to get
its middleware chain built. .check() would have to build/check them
all.

Different error message for when the command's handler function is
unfulfilled vs middlewares.

DisplayOptions/DisplaySpec class? (display name and hidden)

Should Commands have resources like clastic?

# TODO: need to check for middleware provides names + flag names
# conflict

-----

* Command inherit from Parser
* Enable middleware flags
* Ensure top-level middleware flags like --verbose show up for subcommands
* Ensure "builtin" flags like --flagfile and --help show up for all commands
* Make help flag come from HelpHandler
* What to do when the top-level command doesn't have a help_handler,
  but a subcommand does? Maybe dispatch to the subcommand's help
  handler? Would deferring adding the HelpHandler's flag/subcmd help?
  Right now the help flag is parsed and ignored.

---

Notes on making Command inherit from Parser:

The only fuzzy area is when to use prs.get_flag_map() vs
prs._path_flag_map directly. Basically, when filtration-by-usage is
desired, get_flag_map() (or get_flags()) should be used. Only Commands
do this, so it looks a bit weird if you're only looking at the Parser,
where this operation appears to do nothing. This only happens in 1-2
places so probably safe to just comment it for now.

Relatedly, there are some linting errors where it appears the private
_path_flag_map is being accessed. I think these are ok, because these
methods are operating on objects of the same type, so the members are
still technically "protected", in the C++ OOP sense.

"""
