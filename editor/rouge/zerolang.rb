# -*- coding: utf-8 -*- #
# frozen_string_literal: true
#
# Need to copy this file to the lexers dir in the rouge install
# Maybe at: ~/.gem/ruby/2.7.0/gems/rouge-3.22.0/lib/rouge/lexers/zerolang.rb

module Rouge
  module Lexers
    class ZeroLang < RegexLexer
      title 'ZeroLang'
      desc "ZeroLang"
      tag 'zerolang'
      filenames '*.z'
      mimetypes 'application/x-zerolang'

      WORD = /[-!$%&'*+\/<=>?@\\^_|~a-zA-Z0-9]+/


      def self.keywords
        @keywords ||= %w(
          unit
          record
          class
          variant
          union
          facet
          protocol
          data

          function
          in
          out
          is
          as
          if
          when
          then
          else

          for

          while
          loop
          with
          do
          on

          match
          case
          break
          continue
          yield
          return
          swap
          =
        )
      end

      def self.reserved
        @reserved ||= %w(
          macro          
          goto           
          repeat         
          until          
          flag
          cell
          pragma
          switch
          enum
          view
          unsafe
        )
      end

      def self.builtins   # predeclared
        @builtins ||= %w(
          null
          never
          any
          _
          typedef
          tag

          u8 
          u16 
          u32 
          u64 
          u128

          i8 
          i16 
          i32 
          i64 
          i128

          f8 
          f16 
          f32 
          f64 
          f128

          c8
          c32
          string

          true
          false

          public
          private

          this
          meta

          error

          iterator

          take
          borrow
          lock
          generic
        )
      end

      def self.operators  # TODO: remove these
        @operators ||= %w(
        )
      end
          # = 
          # ==   
          # !=   
          # >    
          # <    
          # >=   
          # <= 
          # +    
          # -    
          # *    
          # / 
          # +=   
          # -=   
          # *=   
          # /=
          # and
          # or


      state :root do
        # print "^^^^^^^^^^^^^^^^^^^^^^^^^^start\n"
        # mixin :label    # must be before whitespace
        # rule %r/\(|\)|\s+\{|\}|;/, Punctuation  # must have a space before opening brace
        rule(/\(/, Punctuation, :root)
        rule(/\)/, Punctuation, :pop!)
        rule(/\{/, Punctuation, :root)
        rule(/\}/, Punctuation, :pop!)
        # rule %r/[ \t]+\{|\}|;/, Punctuation  # must have a space before opening brace
                                              # this rule must be above the whitespace rule

        #rule %r/\.\.\./, Keyword  # ellipsis

        mixin :whitespace

        rule %r(#(.*)?\n?), Comment::Single
        # rule %r/-?(?:0|[1-9]\S*)/, Num::Integer
        # rule %r/-?[0-9]\S*/, Num::Integer
        #rule NUMBER, Num::Integer
        # rule NUMBER, Literal::Number

        # rule(/`/, Punctuation, :raw_string)   # backquote syntax was removed
        # re's can't count so specifically handle 5,4,3 double quotes. 
        # we cannot properly handle more than 5 although this is valid in the language
        rule(/"""""/, Punctuation, :raw_string5) # or more than three...
        rule(/""""/, Punctuation, :raw_string4) # or more than three...
        rule(/"""/, Punctuation, :raw_string3) # or more than three...
        rule(/"/, Punctuation, :interpreted_string)

        #rule %r/\{/, Error   # must have a space before opening brace
        #rule %r/[\[\],\\']/, Error
        rule %r/[\[\],;`]/, Error


        mixin :path
        # mixin :name
        #mixin :label_id_id
        mixin :label_id
        # mixin :label_number
        # rule NUMBER, Literal::Number
        mixin :label_id_null
        # mixin :label_number_pre
        mixin :value

          
        #rule WORD do |m|
        #  name = m[0]
        #  token wordtype(name, false)
        #end

        rule %r/\n+/, Text::Whitespace

        rule %r/.*/ do |m|
          print 'Error: ahhh ', m[0], "\n"
          token Error
        end
      end

      #state :object do
      #  mixin :whitespace
      #  mixin :name
      #  mixin :value
      #  rule %r/}/, Punctuation, :pop!
      #  rule %r/,/, Punctuation
      #end

      state :whitespace do
        # rule %r/\s+/, Text::Whitespace
        rule %r/[ \t]+/, Text::Whitespace
      end

      state :path do
        #rule %r/("(?:\\.|[^"\\\n])*?")(\s*)(:)/ do |m|
        rule %r/(\.)\b/, Punctuation  # leading dot is not allowed, but can have path after other literals
        rule %r/(#{WORD})(\.)/ do |m|
          name = m[1]
          #nametype = wordtype(name, Name::Namespace)
          nametype = wordtype(name, Name::Variable)
          groups nametype, Punctuation
        end
      end

      #state :label do
      #  #rule %r/("(?:\\.|[^"\\\n])*?")(\s*)(:)/ do |m|
      #  rule %r/^(\s*)(#{WORD})(\s*)(:)/ do |m|
      #    name = m[2]
      #    nametype = wordtype(name, Name::Label)  # definition
      #    #print "What:", name, nametype, "\n"
      #    groups Text::Whitespace, nametype, Text::Whitespace, Punctuation
      #  end
      #  rule %r/^(\s*)(:)(\s*)(#{WORD})/ do |m|
      #    name = m[4]
      #    nametype = wordtype(name, Name::Label)  # definition
      #    #print "What:", name, nametype, "\n"
      #    groups Text::Whitespace, Punctuation, Text::Whitespace, nametype
      #  end
      #end

      #state :name do
      #  #rule %r/("(?:\\.|[^"\\\n])*?")(\s*)(:)/ do |m|
      #  rule %r/(#{WORD})(\s*)(:)/ do |m|
      #    name = m[1]
      #    nametype = wordtype(name, Name::Tag)  # definition
      #    #print "What:", name, nametype, "\n"
      #    groups nametype, Text::Whitespace, Punctuation
      #  end
      #  rule %r/(:)(\s*)(#{WORD})/ do |m|
      #    name = m[3]
      #    nametype = wordtype(name, Name::Tag)  # definition
      #    # print m[0], "/", m[1], "/", m[2]
      #    # print "What:", name, "=", nametype, "\n"
      #    groups Punctuation, Text::Whitespace, nametype
      #  end
      #end

      # state :label_id_id do
      #   #rule %r/("(?:\\.|[^"\\\n])*?")(\s*)(:)/ do |m|
      #   rule %r/(#{WORD})(::)/, Name::Label
      #   #rule %r/(:)(#{WORD})/ do |m|
      #   #  name = m[1]
      #   #  nametype = wordtype(name, Name::Label)  # definition
      #   #  #print "What:", name, nametype, "\n"
      #   #  groups nametype, Text::Whitespace, Punctuation
      #   #end
      # end 

      state :label_id do
        #rule %r/("(?:\\.|[^"\\\n])*?")(\s*)(:)/ do |m|
        rule %r/#{WORD}\:/, Name::Label
        #rule %r/(#{WORD})(:)/ do |m|
        #  name = m[1]
        #  nametype = wordtype(name, Name::Label)  # definition
        #  #print "What:", name, nametype, "\n"
        #  groups nametype, Text::Whitespace, Punctuation
        #end
      end 

      # state :label_number do
      #   #rule %r/("(?:\\.|[^"\\\n])*?")(\s*)(:)/ do |m|
      #   rule %r/#{NUMBER}\:/, Name::Label
      #   #rule %r/(#{NUMBER})(:)/ do |m|
      #   #  name = m[1]
      #   #  nametype = wordtype(name, Name::Label)  # definition
      #   #  #print "What:", name, nametype, "\n"
      #   #  groups nametype, Text::Whitespace, Punctuation
      #   #end
      # end 


      state :label_id_null do
        #rule %r/("(?:\\.|[^"\\\n])*?")(\s*)(:)/ do |m|
        # rule %r/\:#{WORD}/, Literal::Number
        rule %r/\:#{WORD}/, Name::Label
        #rule %r/(:)(#{WORD})/ do |m|
        #  name = m[1]
        #  nametype = wordtype(name, Name::Label)  # definition
        #  #print "What:", name, nametype, "\n"
        #  groups nametype, Text::Whitespace, Punctuation
        #end
      end 


      # state :label_number_pre do
      #   #rule %r/("(?:\\.|[^"\\\n])*?")(\s*)(:)/ do |m|
      #   rule %r/(:)(#{NUMBER})/, Name::Label
      #   #rule %r/(:)(#{NUMBER})/ do |m|
      #   #  name = m[1]
      #   #  nametype = wordtype(name, Name::Label)  # definition
      #   #  #print "What:", name, nametype, "\n"
      #   #  groups nametype, Text::Whitespace, Punctuation
      #   #end
      # end 





      #state :name do
      #  #rule %r/("(?:\\.|[^"\\\n])*?")(\s*)(:)/ do |m|
      #  rule %r/(#{WORD})(:)/ do |m|
      #    name = m[1]
      #    nametype = wordtype(name, Name::Label)  # definition
      #    #print "What:", name, nametype, "\n"
      #    groups nametype, Text::Whitespace, Punctuation
      #  end
      #  rule %r/(:)(#{WORD})/ do |m|
      #    name = m[3]
      #    nametype = wordtype(name, Name::Label)  # definition
      #    # print m[0], "/", m[1], "/", m[2]
      #    # print "What:", name, "=", nametype, "\n"
      #    groups Punctuation, Text::Whitespace, nametype
      #  end
      #end

      state :value do
        rule WORD do |m|
          name = m[0]
          nametype = wordtype(name, Name::Variable)
          token nametype
        end
      end

      def wordtype(word, defaulttoken)
        # returns the token type given a word
          if self.class.keywords.include? word
            return Keyword
          elsif self.class.reserved.include? word
            # token Keyword::Reserved
            #print "Reserved:", word
            return Error
          elsif self.class.builtins.include? word
            return Name::Builtin
          elsif self.class.operators.include? word
            return Operator
          # elsif word.end_with? "!" or word.end_with? "~"
          #   # special definitions
          #   return Name::Other
          #elsif word.start_with? "@" or word.start_with? "~"
          #  # special definitions
          #  return Name::Other
          else
            return defaulttoken
            #if ispath
            #  return Name::Namespace
            #else
            #  return Name::Label
            #end
          end
      end

      #state :value do
      #  mixin :whitespace
      #  mixin :constants
      #  rule %r/"/, Str::Double, :string
      #  rule %r/\[/, Punctuation, :array
      #  rule %r/{/, Punctuation, :object
      #end

      state :interpreted_string do
        rule %r/"/, Punctuation, :pop!
        rule %r/\\([\\bnrt"']|x[a-fA-F0-9]{2}|u[a-fA-F0-9]{4}|u[a-fA-F0-9]{8})/, Literal::String::Escape
        # rule %r/\\\(.*\)/, Literal::String::Interpol  # interpolated expression
        #rule %r/\\\(/, Punctuation, :root   # have any expression, root will pop on ')'
        rule %r/\\\{/, Punctuation, :root   # have any expression, root will pop on ')'
        rule %r/[^\\"\n]+/, Literal::String
        rule %r/\\\n/, Literal::String
        rule %r/\\/, Error # stray backslash
      end

      state :raw_string3 do
        #rule(/`/,             Punctuation, :pop!)
        rule(/"""/,             Punctuation, :pop!)
        #rule(/[^`]+/m,        Literal::String::Backtick)
        rule(/.*?(?=""")/m,        Literal::String::Backtick)
      end

      state :raw_string4 do
        #rule(/`/,             Punctuation, :pop!)
        rule(/""""/,             Punctuation, :pop!)
        #rule(/[^`]+/m,        Literal::String::Backtick)
        rule(/.*?(?="""")/m,        Literal::String::Backtick)
      end

      state :raw_string5 do
        #rule(/`/,             Punctuation, :pop!)
        rule(/"""""/,             Punctuation, :pop!)
        #rule(/[^`]+/m,        Literal::String::Backtick)
        rule(/.*?(?=""""")/m,        Literal::String::Backtick)
      end

      #state :array do
      #  mixin :value
      #  rule %r/\]/, Punctuation, :pop!
      #  rule %r/,/, Punctuation
      #end

      #state :constants do
      #  rule %r/(?:true|false|null)/, Keyword::Constant
      #  rule %r/-?(?:0|[1-9]\d*)\.\d+(?:e[+-]?\d+)?/i, Num::Float
      #  rule %r/-?(?:0|[1-9]\d*)(?:e[+-]?\d+)?/i, Num::Integer
      #end
    end
  end
end
